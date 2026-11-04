# -*- coding: utf-8 -*-
#
# Phase - Revit pyRevit Extension
# Copyright (C) 2026 Nynsu
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Locação de conduíte - classifica os eletrodutos no parâmetro "Tipo de instalação"
(Sobreposto, Enterrado, Embutido na laje ou Embutido na parede) pela altura da laje.

Ambiente-alvo: Autodesk Revit 2026 + pyRevit (motor IronPython 2.7).
A interface (ui.xaml) é carregada por pyrevit.forms.WPFWindow.

Organização do arquivo:
    1. Importações e constantes
    2. Utilidades
    3. Dados do Revit
    4. Item da lista e linha do quantitativo
    5. Janela: montagem, colunas, filtros, seleção, quantitativo
    6. Janela: gravação, status e painel de resultado
    7. main()
"""

# ==========================================================================
# 1. IMPORTAÇÕES E CONSTANTES
# ==========================================================================
import io
import re
import time
import datetime

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
# Revit 2026 roda em .NET 8: INotifyPropertyChanged fica em System.ObjectModel
# (no .NET Framework ficava em System.dll). Sem isto o import abaixo falha.
for _ref in ('System.ObjectModel', 'System'):
    try:
        clr.AddReference(_ref)
    except Exception:
        pass

import System
from System.Collections.Generic import List
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows import (FontStyles, FontWeights, GridLength, GridUnitType, HorizontalAlignment,
                            Setter, SizeToContent, Style, SystemParameters, Visibility)
from System.Windows.Controls import (CheckBox, ContextMenu, Control, GridView, GridViewColumn,
                                     GridViewColumnHeader, ItemsControl, MenuItem, TextBlock)
from System.Windows.Controls.Primitives import PlacementMode
from System.Windows.Input import Key
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes, Color, SolidColorBrush
from System.Windows.Threading import Dispatcher, DispatcherFrame, DispatcherOperationCallback, DispatcherPriority

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc

try:
    text_type = unicode  # noqa: F821  (IronPython 2.7)
except NameError:
    text_type = str

NOME_FERRAMENTA = u'Locação de conduíte'
XAML_FILE = script.get_bundle_file('ui.xaml')

ALTURA_PADRAO = 3.0                          # metros
# Janela com a lista aberta: altura e altura mínima. Fechada, ela acompanha o conteúdo.
JANELA_ABERTA = (800, 620)
MIN_FECHADA = 320
PARAM_INSTALACAO = u'Tipo de instalação'

SOBREPOSTO = u'SOBREPOSTO'
ENTERRADO = u'ENTERRADO'
EMBUTIDO_LAJE = u'EMBUTIDO NA LAJE'
EMBUTIDO_PAREDE = u'EMBUTIDO NA PAREDE'

SIT_MUDAR = u'Vai mudar'
SIT_IGUAL = u'Sem mudança'
SIT_SEM_ELEVACAO = u'Sem elevação'
SIT_SEM_PARAMETRO = u'Sem parâmetro'
SIT_SOMENTE_LEITURA = u'Somente leitura'
SIT_ERRO = u'Erro de leitura'
# Índice do combo "Situação" -> situações mostradas (None = todas; [] = bloqueadas)
FILTRO_SITUACAO = {0: None, 1: [SIT_MUDAR], 2: [SIT_IGUAL], 3: []}

# chave, propriedade, título no cabeçalho, título no menu, largura, visível ao abrir
COLUNAS = [
    ('tipo', 'Tipo', u'Tipo', u'Tipo', 240, True),
    ('diametro', 'Diametro', u'Diâm. (mm)', u'Diâmetro', 80, True),
    ('elevacao', 'Elevacao', u'Elev. (m)', u'Elevação', 75, True),
    ('comprimento', 'Comprimento', u'Compr. (m)', u'Comprimento', 80, True),
    ('nivel', 'Nivel', u'Nível', u'Nível', 120, False),
    ('atual', 'Atual', u'Valor atual', u'Valor atual', 150, True),
    ('novo', 'Novo', u'Valor novo', u'Valor novo', 150, True),
    ('situacao', 'Situacao', u'Situação', u'Situação', 110, True),
]

# propriedade, título, largura (quadro de quantitativo)
COLUNAS_QUANT = [
    ('Sobreposto', u'Sobreposto', 100),
    ('Enterrado', u'Enterrado', 100),
    ('Laje', u'Emb. na laje', 110),
    ('Parede', u'Emb. na parede', 120),
    ('Total', u'Total', 100),
]
PROP_QUANT = {SOBREPOSTO: 'Sobreposto', ENTERRADO: 'Enterrado',
              EMBUTIDO_LAJE: 'Laje', EMBUTIDO_PAREDE: 'Parede'}


def brush(hexa):
    """'#RRGGBB' ou '#AARRGGBB' -> SolidColorBrush."""
    h = hexa.lstrip('#')
    if len(h) == 6:
        h = 'FF' + h
    return SolidColorBrush(Color.FromArgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)))


# Estados de linha (a seleção amarela é feita no XAML, por cima destas cores)
BRUSH_ALTERADO = brush('#FFC98B')        # gravado nesta sessão (só o fundo, sem negrito)
BG_SOMENTE_LEITURA = brush('#12000000')  # camada escura por cima da cor da linha
FG_SOMENTE_LEITURA = brush('#3F3F3F')    # + itálico
BG_DIAMETRO = brush('#EEF3F9')           # linhas abertas de um tipo, no quantitativo
COR_DICA = brush('#5A6778')
COR_ERRO = brush('#A93232')


# ==========================================================================
# 2. UTILIDADES
# ==========================================================================
def to_text(value):
    if value is None:
        return u''
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(str(value))
        except Exception:
            return u''


def eid_value(element_id):
    """Inteiro de um ElementId (Revit 2026: ElementId.Value, Int64)."""
    try:
        return int(element_id.Value)
    except Exception:
        return -1


def natural_key(text):
    """Ordenação natural: 'A-2' antes de 'A-10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', to_text(text))]


def is_checked(ctrl):
    return ctrl.IsChecked == True  # noqa: E712 (Nullable<bool>)


def pump_ui():
    """Processa a fila da interface (status, progresso, botão Cancelar) sem sair
    da thread do Revit. Chamar dentro de laços longos."""
    try:
        frame = DispatcherFrame()

        def _sair(f):
            f.Continue = False
            return None
        Dispatcher.CurrentDispatcher.BeginInvoke(
            DispatcherPriority.Background, DispatcherOperationCallback(_sair), frame)
        Dispatcher.PushFrame(frame)
    except Exception:
        pass


def param_text(p):
    try:
        if p is None:
            return u''
        if p.StorageType == DB.StorageType.String:
            return to_text(p.AsString())
        return to_text(p.AsValueString())
    except Exception:
        return u''


def sanitize_filename(nome, padrao=u'Sem_nome'):
    limpo = re.sub(u'[\\\\/:*?"<>|]', u'_', to_text(nome)).strip(u' .')
    return limpo or padrao


def numero(valor, casas=2):
    """Número com vírgula decimal, como o projetista lê: 3,12."""
    return u'{0:.{1}f}'.format(valor, casas).replace(u'.', u',')


def ler_altura(texto):
    """Altura digitada em metros (aceita vírgula ou ponto). None se inválida."""
    try:
        valor = float(to_text(texto).strip().replace(u',', u'.'))
    except ValueError:
        return None
    return valor if valor > 0 else None


def metros_para_interno(valor):
    return DB.UnitUtils.ConvertToInternalUnits(valor, DB.UnitTypeId.Meters)


def interno_para_metros(valor):
    return DB.UnitUtils.ConvertFromInternalUnits(valor, DB.UnitTypeId.Meters)


def nome_elemento(el):
    """Nome legível para a tabela de problemas: o tipo ou o nome do elemento."""
    try:
        tipo = doc.GetElement(el.GetTypeId())
        if tipo is not None:
            return to_text(DB.Element.Name.GetValue(tipo))
    except Exception:
        pass
    try:
        return to_text(DB.Element.Name.GetValue(el)) or to_text(el.Category.Name)
    except Exception:
        return u'—'


class LinhaProblema(object):
    """Linha da tabela de problemas do painel de resultado.
    Guarda o id e o nome, NÃO o objeto: o elemento pode ficar inválido depois de um RollBack."""

    def __init__(self, el, problema):
        self.eid = None
        self.Id = u'—'
        self.Elemento = u'—'
        if el is not None:
            try:
                self.eid = eid_value(el.Id)
                self.Id = to_text(self.eid)
                self.Elemento = nome_elemento(el)
            except Exception:
                pass
        self.Problema = to_text(problema)

    def elemento(self):
        """Busca o elemento de novo pelo id (None se ele não existe mais)."""
        if self.eid is None:
            return None
        try:
            el = doc.GetElement(DB.ElementId(System.Int64(self.eid)))
            return el if el is not None and el.IsValidObject else None
        except Exception:
            return None


def make_template(inner):
    xmlns = (u'xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
             u'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')
    return XamlReader.Parse(u'<DataTemplate {}>{}</DataTemplate>'.format(xmlns, inner))


CELULA_TEXTO = (u'<TextBlock Text="{{Binding {prop}, Mode=OneWay}}" FontStyle="{{Binding Estilo, Mode=OneWay}}" '
                u'Foreground="{{Binding Fg, Mode=OneWay}}" TextTrimming="CharacterEllipsis" '
                u'VerticalAlignment="Center" Padding="2,1"/>')
CELULA_QUANT = (u'<TextBlock Text="{{Binding {prop}, Mode=OneWay}}" FontWeight="{{Binding Peso, Mode=OneWay}}" '
                u'TextAlignment="{alinhar}" TextTrimming="CharacterEllipsis" Padding="2,1"/>')
# Marcado = selecionado. Linha bloqueada: caixa desativada.
CELULA_SELECAO = (u'<CheckBox IsChecked="{Binding IsSelected, Mode=TwoWay, '
                  u'RelativeSource={RelativeSource AncestorType=ListViewItem}}" '
                  u'IsEnabled="{Binding Selecionavel, Mode=OneWay}" '
                  u'HorizontalAlignment="Center" Focusable="False" Margin="0"/>')


# ==========================================================================
# 3. DADOS DO REVIT
# ==========================================================================
def coletar_eletrodutos():
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Conduit)
                .WhereElementIsNotElementType()
                .ToElements())


def nome_tipo(tipo_elem):
    """Só o nome do tipo, como aparece nas tabelas."""
    try:
        return to_text(DB.Element.Name.GetValue(tipo_elem))
    except Exception:
        return u''


def nome_familia_tipo(tipo_elem):
    """Retorna 'Família Tipo' como texto, de forma segura."""
    if tipo_elem is None:
        return u''
    partes = []
    try:
        partes.append(tipo_elem.FamilyName)
    except Exception:
        pass
    try:
        partes.append(DB.Element.Name.GetValue(tipo_elem))
    except Exception:
        pass
    return u' '.join([to_text(p) for p in partes if p])


def ler_double(elem, bip, nome_pt=None):
    """Tenta pelo BuiltInParameter; se falhar, pelo nome do parâmetro."""
    p = elem.get_Parameter(bip)
    if (p is None or not p.HasValue) and nome_pt:
        lista = elem.GetParameters(nome_pt)
        p = lista[0] if lista.Count > 0 else None
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double:
        return p.AsDouble()
    return None


def ler_elevacao(conduit):
    """Elevação em pés: a intermediária ou, sem ela, a média das extremidades.
    None quando o eletroduto não tem nenhuma das duas (nunca assumir zero)."""
    z = ler_double(conduit, DB.BuiltInParameter.RBS_OFFSET_PARAM, u'Elevação intermediária')
    if z is not None:
        return z
    pontas = [ler_double(conduit, DB.BuiltInParameter.RBS_START_OFFSET_PARAM,
                         u'Elevação da parte de cima da extremidade superior'),
              ler_double(conduit, DB.BuiltInParameter.RBS_END_OFFSET_PARAM,
                         u'Elevação da parte de baixo da extremidade inferior')]
    pontas = [v for v in pontas if v is not None]
    return sum(pontas) / len(pontas) if pontas else None


def classificar(texto_familia, z, limite):
    """Regra da ferramenta. None quando falta a elevação de que a regra precisa."""
    if u'Rígido' in texto_familia or u'SEALTUBO' in texto_familia:
        return SOBREPOSTO
    if z is None:
        return None
    if z < 0:
        return ENTERRADO
    if z > limite:
        return EMBUTIDO_LAJE
    return EMBUTIDO_PAREDE


# ==========================================================================
# 4. ITEM DA LISTA E LINHA DO QUANTITATIVO
# ==========================================================================
class Item(INotifyPropertyChanged):
    """Um eletroduto da lista. Propriedades com notificação atualizam a tela sozinhas."""

    def __init__(self, el, limite):
        self._handlers = []
        self.el = el
        self.Chave = eid_value(el.Id)
        self.Id = to_text(self.Chave)
        self.alterado = False
        self.limite = limite
        self.resultado = None
        self.motivo = u''
        self.Tipo = u''
        self.texto_regra = u''     # família + tipo: só para a regra (Rígido, SEALTUBO)
        self.Diametro = u'—'
        self.Elevacao = u'—'
        self.Comprimento = u'—'
        self.Nivel = u''
        self.Atual = u'—'
        self.Novo = u'—'
        self.Situacao = u''
        self.diam_mm = None
        self.comp_m = 0.0
        self.z = None
        self.valor_atual = None
        self.bloqueio = None
        self.ler()

    # INotifyPropertyChanged (padrão IronPython)
    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def notificar(self, *nomes):
        for nome in nomes:
            args = PropertyChangedEventArgs(nome)
            for h in list(self._handlers):
                h(self, args)

    def ler(self):
        """Lê o eletroduto no modelo e recalcula a linha."""
        self.bloqueio = None
        try:
            self._ler_valores()
        except Exception as ex:
            self.bloqueio = (SIT_ERRO, to_text(ex))
        self.calcular(self.limite)
        self.notificar('Tipo', 'Diametro', 'Elevacao', 'Comprimento', 'Nivel', 'Atual')

    def _ler_valores(self):
        el = self.el
        tipo = doc.GetElement(el.GetTypeId())
        self.Tipo = nome_tipo(tipo)
        self.texto_regra = nome_familia_tipo(tipo)
        self.diam_mm = None
        diam = ler_double(el, DB.BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM)
        if diam is not None:
            self.diam_mm = DB.UnitUtils.ConvertFromInternalUnits(diam, DB.UnitTypeId.Millimeters)
            casas = 0 if abs(self.diam_mm - round(self.diam_mm)) < 0.05 else 1
            self.Diametro = numero(self.diam_mm, casas)
        comp = ler_double(el, DB.BuiltInParameter.CURVE_ELEM_LENGTH)
        self.comp_m = interno_para_metros(comp) if comp is not None else 0.0
        self.Comprimento = numero(self.comp_m) if comp is not None else u'—'
        self.z = ler_elevacao(el)
        self.Elevacao = numero(interno_para_metros(self.z)) if self.z is not None else u'—'
        self.Nivel = param_text(el.get_Parameter(DB.BuiltInParameter.RBS_START_LEVEL_PARAM))

        self.valor_atual = None
        self.Atual = u'—'
        p = el.LookupParameter(PARAM_INSTALACAO)
        if p is None:
            self.bloqueio = (SIT_SEM_PARAMETRO,
                             u'O parâmetro "{}" não foi encontrado neste eletroduto.'.format(PARAM_INSTALACAO))
        elif p.StorageType != DB.StorageType.String:
            self.bloqueio = (SIT_SOMENTE_LEITURA,
                             u'O parâmetro "{}" não é do tipo texto.'.format(PARAM_INSTALACAO))
        else:
            self.valor_atual = p.AsString()
            self.Atual = to_text(self.valor_atual) or u'(vazio)'
            if p.IsReadOnly:
                self.bloqueio = (SIT_SOMENTE_LEITURA,
                                 u'O parâmetro "{}" é somente leitura.'.format(PARAM_INSTALACAO))

    def calcular(self, limite):
        """Recalcula Valor novo e Situação para a altura informada, sem reler o modelo."""
        self.limite = limite
        self.resultado = classificar(self.texto_regra, self.z, limite) if self.bloqueio is None or \
            self.bloqueio[0] != SIT_ERRO else None
        self.Novo = self.resultado or u'—'
        if self.bloqueio is not None:
            self.Situacao, self.motivo = self.bloqueio
        elif self.resultado is None:
            self.Situacao = SIT_SEM_ELEVACAO
            self.motivo = u'O eletroduto não tem elevação para ser classificado.'
        else:
            self.motivo = u''
            self.Situacao = SIT_MUDAR if self.valor_atual != self.resultado else SIT_IGUAL
        self.notificar('Novo', 'Situacao', 'Dica', 'Selecionavel', 'RowBrush', 'Fg', 'Estilo')

    def marcar_alterado(self):
        self.alterado = True
        self.notificar('RowBrush')

    @property
    def Selecionavel(self):
        return self.Situacao in (SIT_MUDAR, SIT_IGUAL)

    @property
    def RowBrush(self):
        if not self.Selecionavel:
            return BG_SOMENTE_LEITURA
        if self.alterado:
            return BRUSH_ALTERADO
        return None     # None = mantém a alternância branco/#F7F9FB

    @property
    def Fg(self):
        return Brushes.Black if self.Selecionavel else FG_SOMENTE_LEITURA

    @property
    def Estilo(self):
        return FontStyles.Normal if self.Selecionavel else FontStyles.Italic

    @property
    def Dica(self):
        base = u'{}  (Id {})'.format(self.Tipo, self.Id)
        return u'{}\n{}'.format(base, self.motivo) if self.motivo else base

    def texto_busca(self):
        return u' '.join([self.Tipo, self.Diametro, self.Elevacao, self.Comprimento, self.Nivel,
                          self.Atual, self.Novo, self.Situacao, self.Id]).lower()


class LinhaQuantitativo(object):
    """Uma linha do quadro: o tipo de eletroduto (clicável) ou um diâmetro dele."""

    def __init__(self, nome, valores, familia=None, aberto=False):
        self.familia = familia       # só a linha do tipo abre e fecha
        self.Sobreposto = numero(valores.get('Sobreposto', 0.0))
        self.Enterrado = numero(valores.get('Enterrado', 0.0))
        self.Laje = numero(valores.get('Laje', 0.0))
        self.Parede = numero(valores.get('Parede', 0.0))
        self.Total = numero(sum(valores.values()))
        self.Peso = FontWeights.Normal
        if familia is not None:
            self.Nome = u'{} {}'.format(u'▾' if aberto else u'▸', nome)
            self.RowBrush = None
            self.Dica = u'{}\nClique para {} os diâmetros.'.format(nome, u'recolher' if aberto else u'ver')
        else:
            self.Nome = u'      ' + nome
            self.RowBrush = BG_DIAMETRO
            self.Dica = None


# ==========================================================================
# 5. JANELA: MONTAGEM, COLUNAS, FILTROS, SELEÇÃO, QUANTITATIVO
# ==========================================================================
class Janela(forms.WPFWindow):

    def __init__(self, itens):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._carregando = True
        self.itens = sorted(itens, key=lambda i: (natural_key(i.Tipo), i.diam_mm or 0, i.Chave))
        self.marcados = set(i.Chave for i in self.itens if i.Selecionavel)   # tudo marcado ao abrir
        self.limite = metros_para_interno(ALTURA_PADRAO)
        self.ocupado = False
        self.cancelar = False
        self.descartar = False
        self.problemas = []
        self.expandidos = set()      # tipos abertos no quantitativo
        self.lista_aberta = False
        self.altura_aberta = JANELA_ABERTA[0]
        self.altura_fechada = None

        self.TxtContexto.Text = u'Projeto ativo: {}'.format(doc.Title)
        self.TxtAltura.Text = numero(ALTURA_PADRAO)
        self.montar_colunas()
        self.montar_menu_colunas()
        self.montar_quantitativo()

        # eventos - altura e filtros
        self.TxtAltura.KeyDown += self.altura_tecla
        self.TxtAltura.LostFocus += lambda s, e: self.atualizar_altura()
        self.TxtPesquisa.TextChanged += lambda s, e: self.aplicar_filtro()
        self.CmbSituacao.SelectionChanged += lambda s, e: self.aplicar_filtro()
        self.ChkSomenteMarcados.Checked += lambda s, e: self.aplicar_filtro()
        self.ChkSomenteMarcados.Unchecked += lambda s, e: self.aplicar_filtro()
        # eventos - lista
        self.BtnMarcarVisiveis.Click += lambda s, e: self.LvItens.SelectAll()
        self.BtnDesmarcarVisiveis.Click += lambda s, e: self.LvItens.UnselectAll()
        self.BtnInverter.Click += self.inverter
        self.BtnColunas.Click += self.abrir_menu_colunas
        self.BrdEletrodutos.MouseLeftButtonUp += self.alternar_lista
        self.LvItens.SelectionChanged += self.selecao_mudou
        self.LvQuantitativo.SelectionChanged += self.quantitativo_sem_selecao
        self.LvQuantitativo.PreviewMouseLeftButtonUp += self.quantitativo_click
        # eventos - rodapé e resultado
        self.BtnGravar.Click += self.gravar
        self.BtnCancelar.Click += self.cancelar_click
        self.BtnCancelarDescartar.Click += self.cancelar_descartar_click
        self.BtnFechar.Click += lambda s, e: self.Close()
        self.BtnResultadoOk.Click += lambda s, e: self.fechar_resultado()
        self.BtnResultadoSelecionar.Click += self.selecionar_problemas
        self.BtnResultadoTxt.Click += self.salvar_problemas_txt
        self.Closing += self.ao_fechar

        self._carregando = False
        self.aplicar_filtro()

    # ---------------------------------------------------------------- colunas
    def estilo_cabecalho(self, alinhamento, padding=None):
        base = self.TryFindResource(GridViewColumnHeader)
        estilo = Style(GridViewColumnHeader, base) if base is not None else Style(GridViewColumnHeader)
        estilo.Setters.Add(Setter(Control.HorizontalContentAlignmentProperty, alinhamento))
        if padding is not None:
            estilo.Setters.Add(Setter(Control.PaddingProperty, padding))
        return estilo

    def montar_colunas(self):
        self.gv = GridView()
        self.col_selecao = GridViewColumn()
        self.ChkCabecalho = CheckBox()
        self.ChkCabecalho.ToolTip = u'Marcar / desmarcar todos os listados'
        self.ChkCabecalho.Margin = System.Windows.Thickness(0)
        self.ChkCabecalho.Click += self.cabecalho_click
        self.col_selecao.Header = self.ChkCabecalho
        self.col_selecao.Width = 34
        self.col_selecao.HeaderContainerStyle = self.estilo_cabecalho(
            HorizontalAlignment.Center, System.Windows.Thickness(0))
        self.col_selecao.CellTemplate = make_template(CELULA_SELECAO)
        self.gv.Columns.Add(self.col_selecao)

        self.colunas = {}
        self.visivel = {}
        for chave, prop, titulo, _, largura, visivel in COLUNAS:
            col = GridViewColumn()
            col.Header = self.cabecalho_coluna(chave, titulo)
            col.Width = largura
            col.CellTemplate = make_template(CELULA_TEXTO.format(prop=prop))
            self.colunas[chave] = col
            self.visivel[chave] = visivel
        self.LvItens.View = self.gv
        self.aplicar_colunas()

    def cabecalho_coluna(self, chave, titulo):
        if chave != 'situacao':
            return titulo
        cab = TextBlock()
        cab.Text = titulo
        cab.ToolTip = (u'Vai mudar: o valor novo será gravado.\n'
                       u'Sem mudança: o eletroduto já está com esse valor.\n'
                       u'Sem elevação, Sem parâmetro, Somente leitura ou Erro de leitura: não pode ser alterado.')
        return cab

    def aplicar_colunas(self):
        """Mantém a coluna da caixa ✓ e refaz as demais na ordem padrão."""
        while self.gv.Columns.Count > 1:
            self.gv.Columns.RemoveAt(1)
        for coluna in COLUNAS:
            chave = coluna[0]
            if self.visivel[chave]:
                self.gv.Columns.Add(self.colunas[chave])

    def montar_menu_colunas(self):
        self.MenuColunas = ContextMenu()
        for chave, _, _, titulo_menu, _, _ in COLUNAS:
            mi = MenuItem()
            mi.Header = titulo_menu
            mi.IsCheckable = True
            mi.StaysOpenOnClick = True
            mi.IsChecked = self.visivel[chave]
            mi.Tag = chave
            mi.Click += self.coluna_click
            self.MenuColunas.Items.Add(mi)

    def abrir_menu_colunas(self, sender, e):
        self.MenuColunas.PlacementTarget = self.BtnColunas
        self.MenuColunas.Placement = PlacementMode.Bottom
        self.MenuColunas.IsOpen = True

    def coluna_click(self, sender, e):
        self.visivel[sender.Tag] = bool(sender.IsChecked)
        self.aplicar_colunas()

    def montar_quantitativo(self):
        gv = GridView()
        nome = GridViewColumn()
        nome.Header = u'Tipo de eletroduto'
        nome.Width = 400
        nome.CellTemplate = make_template(CELULA_QUANT.format(prop='Nome', alinhar='Left'))
        gv.Columns.Add(nome)
        estilo = self.estilo_cabecalho(HorizontalAlignment.Right)
        for prop, titulo, largura in COLUNAS_QUANT:
            col = GridViewColumn()
            col.Header = titulo
            col.Width = largura
            col.HeaderContainerStyle = estilo
            col.CellTemplate = make_template(CELULA_QUANT.format(prop=prop, alinhar='Right'))
            gv.Columns.Add(col)
        self.LvQuantitativo.View = gv

    def quantitativo_sem_selecao(self, sender, e):
        """O quadro só informa: nenhuma linha fica selecionada."""
        if self.LvQuantitativo.SelectedItems.Count:
            self.LvQuantitativo.UnselectAll()

    def quantitativo_click(self, sender, e):
        """Clicar no tipo abre ou fecha os diâmetros dele."""
        linha = ItemsControl.ContainerFromElement(self.LvQuantitativo, e.OriginalSource)
        familia = linha.Content.familia if linha is not None else None
        if familia is None:
            return
        if familia in self.expandidos:
            self.expandidos.discard(familia)
        else:
            self.expandidos.add(familia)
        self.atualizar_quantitativo(self.alvos())

    def cabecalho_click(self, sender, e):
        selecionaveis = [i for i in self.visiveis() if i.Selecionavel]
        marcados = [i for i in selecionaveis if i.Chave in self.marcados]
        if selecionaveis and len(marcados) == len(selecionaveis):
            self.LvItens.UnselectAll()
        else:
            self.LvItens.SelectAll()

    # ---------------------------------------------------------------- abrir e fechar a lista
    def alternar_lista(self, sender, e):
        self.lista_aberta = not self.lista_aberta
        aberta = self.lista_aberta
        self.PnlEletrodutos.Visibility = Visibility.Visible if aberta else Visibility.Collapsed
        self.RowLista.Height = GridLength(1, GridUnitType.Star) if aberta else GridLength(0)
        self.TxtEletrodutos.Text = u'▾ Eletrodutos' if aberta else u'▸ Eletrodutos'
        area = SystemParameters.WorkArea
        if aberta:
            self.altura_fechada = self.ActualHeight
            self.SizeToContent = SizeToContent.Manual
            self.MinHeight = JANELA_ABERTA[1]
            nova = min(self.altura_aberta, area.Height)
            # cresce para os dois lados, sem sair da área útil da tela
            self.Top = max(area.Top, self.Top - (nova - self.ActualHeight) / 2.0)
            self.Height = nova
        else:
            self.altura_aberta = self.ActualHeight
            self.Top += (self.ActualHeight - self.altura_fechada) / 2.0
            self.MinHeight = MIN_FECHADA
            self.SizeToContent = SizeToContent.Height    # sem espaço vazio abaixo do quantitativo

    # ---------------------------------------------------------------- altura
    def altura_tecla(self, sender, e):
        if e.Key == Key.Return:
            self.atualizar_altura()
            e.Handled = True

    def atualizar_altura(self):
        """Recalcula a lista e o quantitativo. Devolve False se a altura for inválida."""
        altura = ler_altura(self.TxtAltura.Text)
        if altura is None:
            self.TxtAlturaDica.Text = u'Valor inválido: digite uma altura maior que zero, como 3,00.'
            self.TxtAlturaDica.Foreground = COR_ERRO
            return False
        self.TxtAlturaDica.Text = u'Enter atualiza a coluna Valor novo e o quantitativo.'
        self.TxtAlturaDica.Foreground = COR_DICA
        self.limite = metros_para_interno(altura)
        for item in self.itens:
            item.calcular(self.limite)
        self.aplicar_filtro()
        return True

    # ---------------------------------------------------------------- filtros
    def passa_filtro(self, item, texto, situacoes, so_marcados):
        if so_marcados and item.Chave not in self.marcados:
            return False
        if situacoes is not None:
            if situacoes and item.Situacao not in situacoes:
                return False
            if not situacoes and item.Selecionavel:
                return False
        return not texto or texto in item.texto_busca()

    def aplicar_filtro(self):
        if self._carregando:
            return
        texto = to_text(self.TxtPesquisa.Text).strip().lower()
        situacoes = FILTRO_SITUACAO.get(self.CmbSituacao.SelectedIndex)
        so_marcados = is_checked(self.ChkSomenteMarcados)
        visiveis = [i for i in self.itens if self.passa_filtro(i, texto, situacoes, so_marcados)]
        self._carregando = True
        try:
            self.LvItens.ItemsSource = visiveis
            for i in visiveis:               # restaura a marcação depois de filtrar
                if i.Chave in self.marcados:
                    self.LvItens.SelectedItems.Add(i)
        finally:
            self._carregando = False
        self.atualizar_contagem()

    # ---------------------------------------------------------------- seleção
    def selecao_mudou(self, sender, e):
        if self._carregando:
            return
        bloqueados = []
        for i in e.AddedItems:
            if i.Selecionavel:
                self.marcados.add(i.Chave)
            else:
                bloqueados.append(i)
        for i in e.RemovedItems:
            self.marcados.discard(i.Chave)
        if bloqueados:       # Marcar visíveis ou Shift pegam tudo: linha bloqueada não fica marcada
            self._carregando = True
            try:
                for i in bloqueados:
                    self.LvItens.SelectedItems.Remove(i)
            finally:
                self._carregando = False
        self.atualizar_contagem()

    def inverter(self, sender, e):
        visiveis = self.visiveis()
        alvo = [i for i in visiveis if i.Selecionavel and i.Chave not in self.marcados]
        self._carregando = True
        try:
            self.LvItens.UnselectAll()
            self.marcados -= set(i.Chave for i in visiveis)
            for i in alvo:
                self.marcados.add(i.Chave)
                self.LvItens.SelectedItems.Add(i)
        finally:
            self._carregando = False
        self.atualizar_contagem()

    def visiveis(self):
        return list(self.LvItens.ItemsSource or [])

    def alvos(self):
        return [i for i in self.visiveis() if i.Selecionavel and i.Chave in self.marcados]

    def atualizar_contagem(self):
        vis = self.visiveis()
        marcados = self.alvos()
        selecionaveis = len([i for i in vis if i.Selecionavel])
        self.TxtContagem.Text = u'{} selecionado(s)  ·  {} listado(s)  ·  {} no total'.format(
            len(marcados), len(vis), len(self.itens))
        bloqueados = len([i for i in self.itens if not i.Selecionavel])
        resumo = [u'{} selecionado(s)'.format(len(marcados))]
        if bloqueados:
            resumo.append(u'{} não pode(m) ser alterado(s)'.format(bloqueados))
        resumo.append(u'{} no total'.format(len(self.itens)))
        self.TxtResumoEletrodutos.Text = u'  ·  '.join(resumo)
        # cabeçalho em três estados: todos / nenhum / parcial
        if not marcados:
            self.ChkCabecalho.IsChecked = False
        elif len(marcados) == selecionaveis:
            self.ChkCabecalho.IsChecked = True
        else:
            self.ChkCabecalho.IsChecked = None
        self.atualizar_quantitativo(marcados)

    # ---------------------------------------------------------------- quantitativo
    def atualizar_quantitativo(self, marcados):
        """Soma os metros das linhas marcadas pelo Valor novo, por tipo; diâmetro ao abrir."""
        grupos = {}
        totais = {}
        for item in marcados:
            prop = PROP_QUANT.get(item.resultado)
            if prop is None:
                continue
            # arredonda para diferenças mínimas de conversão não abrirem duas linhas
            diam = round(item.diam_mm, 1) if item.diam_mm is not None else None
            valores = grupos.setdefault(item.Tipo, {}).setdefault(diam, {})
            valores[prop] = valores.get(prop, 0.0) + item.comp_m
            totais[prop] = totais.get(prop, 0.0) + item.comp_m

        linhas = []
        for familia in sorted(grupos.keys(), key=natural_key):
            soma = {}
            for valores in grupos[familia].values():
                for prop, metros in valores.items():
                    soma[prop] = soma.get(prop, 0.0) + metros
            aberto = familia in self.expandidos
            linhas.append(LinhaQuantitativo(familia, soma, familia=familia, aberto=aberto))
            if aberto:
                for diam in sorted(grupos[familia].keys(), key=lambda d: d or 0):
                    linhas.append(LinhaQuantitativo(self.nome_diametro(diam), grupos[familia][diam]))
        self.LvQuantitativo.ItemsSource = linhas
        self.LvQuantitativo.Visibility = Visibility.Visible if linhas else Visibility.Collapsed
        self.TxtQuantVazio.Visibility = Visibility.Collapsed if linhas else Visibility.Visible
        self.mostrar_totais(totais)

    def nome_diametro(self, diam):
        if diam is None:
            return u'(sem diâmetro)'
        return u'{} mm'.format(numero(diam, 0 if abs(diam - round(diam)) < 0.05 else 1))

    def mostrar_totais(self, totais):
        partes = [u'{} {}'.format(titulo, numero(totais.get(prop, 0.0)))
                  for prop, titulo, _ in COLUNAS_QUANT[:-1]]
        partes.append(u'Total {}'.format(numero(sum(totais.values()))))
        self.TxtTotais.Text = u'Total (m):  ' + u'  ·  '.join(partes)

    # ==========================================================================
    # 6. JANELA: GRAVAÇÃO, STATUS E PAINEL DE RESULTADO
    # ==========================================================================
    def status(self, texto):
        self.TxtStatus.Text = to_text(texto)

    def set_ocupado(self, ocupado):
        """Durante a gravação só os botões de cancelar ficam ativos."""
        self.ocupado = ocupado
        self.PnlProgresso.Visibility = Visibility.Visible if ocupado else Visibility.Collapsed
        self.GrdCorpo.IsEnabled = not ocupado
        self.BtnGravar.IsEnabled = not ocupado
        self.BtnFechar.IsEnabled = not ocupado
        vis = Visibility.Visible if ocupado else Visibility.Collapsed
        self.BtnCancelar.Visibility = vis
        self.BtnCancelarDescartar.Visibility = vis

    def cancelar_click(self, sender, e):
        self.cancelar = True
        self.status(u'Cancelando depois do item em andamento...')

    def cancelar_descartar_click(self, sender, e):
        if forms.alert(u'Parar e DESFAZER tudo o que foi feito nesta execução?',
                       title=u'Cancelar e descartar', yes=True, no=True):
            self.cancelar = True
            self.descartar = True

    def gravar(self, sender, e):
        if not self.atualizar_altura():
            forms.alert(u'Digite uma altura da laje maior que zero, como 3,00.', title=u'Aviso')
            return
        alvos = self.alvos()
        if not alvos:
            forms.alert(u'Marque ao menos um eletroduto na lista de eletrodutos.', title=u'Aviso')
            return
        mudar = [i for i in alvos if i.Situacao == SIT_MUDAR]
        if not mudar:
            forms.alert(u'Os eletrodutos marcados já estão com o valor certo.', title=u'Aviso')
            return
        resumo = {}
        for item in mudar:
            resumo[item.resultado] = resumo.get(item.resultado, 0) + 1
        linhas = [u'{}: {}'.format(valor, resumo[valor]) for valor in sorted(resumo.keys())]
        if not forms.alert(u'Gravar "{}" em {} eletroduto(s)?\n\n{}'.format(
                PARAM_INSTALACAO, len(mudar), u'\n'.join(linhas)),
                title=u'Confirmar', yes=True, no=True):
            return
        self.executar(alvos, mudar)

    def executar(self, alvos, mudar):
        inicio = time.time()
        self.cancelar = False
        self.descartar = False
        self.set_ocupado(True)
        problemas = []
        gravados = set()
        falhas = set()
        total = len(mudar)
        erro_geral = False
        t = None
        tg = DB.TransactionGroup(doc, NOME_FERRAMENTA)
        tg.Start()
        try:
            t, avisos = transacao(NOME_FERRAMENTA)
            t.Start()
            for n, item in enumerate(mudar, 1):
                if self.cancelar:
                    break
                try:
                    p = item.el.LookupParameter(PARAM_INSTALACAO)
                    if not p.Set(item.resultado):
                        raise Exception(u'O Revit não aceitou o valor "{}".'.format(item.resultado))
                    gravados.add(item.Chave)
                except Exception as ex:
                    falhas.add(item.Chave)
                    problemas.append((item.el, to_text(ex)))
                self.PbProgresso.Value = float(n) / total
                if n % 10 == 0 or n == total:
                    self.status(u'Gravando: {}/{}'.format(n, total))
                    pump_ui()
            if t.Commit() != DB.TransactionStatus.Committed:
                raise Exception(u'O Revit não confirmou a gravação.')
            for aviso in sorted(set(avisos.avisos)):
                problemas.append((None, u'Aviso do Revit: ' + aviso))
            if self.descartar:
                tg.RollBack()
            else:
                tg.Assimilate()       # vira um único passo no Ctrl+Z do Revit
        except Exception as ex:
            if t is not None and t.HasStarted() and not t.HasEnded():
                t.RollBack()
            if tg.HasStarted() and not tg.HasEnded():
                tg.RollBack()
            erro_geral = True
            gravados = set()
            problemas.append((None, u'Erro geral (nada foi alterado): {}'.format(ex)))
        finally:
            self.set_ocupado(False)
            self.PbProgresso.Value = 0

        if self.descartar or erro_geral:
            gravados = set()
        for item in mudar:
            item.ler()
            if item.Chave in gravados:
                item.marcar_alterado()
        # Conferidos sem falha saem da marcação; os que falharam continuam marcados.
        if not self.descartar and not erro_geral:
            self.marcados -= gravados
            if not self.cancelar:
                self.marcados -= set(i.Chave for i in alvos if i.Chave not in falhas)
        self.aplicar_filtro()
        self.resultado_final(alvos, mudar, gravados, problemas, time.time() - inicio, erro_geral)

    def resultado_final(self, alvos, mudar, gravados, problemas, tempo, erro_geral):
        if self.descartar:
            self.mostrar_resultado([u'Execução cancelada e descartada: nada foi alterado.'], problemas, True)
            return
        if erro_geral:
            self.mostrar_resultado([u'Nada foi alterado.'], problemas)
            return
        linhas = [u'{} eletroduto(s) alterado(s).'.format(len(gravados))]
        iguais = len(alvos) - len(mudar)
        if iguais:
            linhas.append(u'{} já estavam com o valor certo.'.format(iguais))
        marcados_chaves = set(i.Chave for i in alvos)
        desmarcados = len([i for i in self.itens
                           if i.Situacao == SIT_MUDAR and i.Chave not in marcados_chaves])
        if desmarcados and not self.cancelar:
            linhas.append(u'{} iam mudar, mas estavam desmarcados.'.format(desmarcados))
        if self.cancelar:
            linhas.append(u'Cancelado pelo usuário (o que já tinha sido feito foi mantido).')
        linhas.append(u'Tempo: {} s'.format(numero(tempo)))
        self.mostrar_resultado(linhas, problemas, self.cancelar)

    def mostrar_resultado(self, linhas, problemas=None, cancelado=False):
        """Painel por cima da janela. problemas: [(elemento ou None, u'motivo'), ...]"""
        problemas = problemas or []
        if problemas:
            self.BrdResultadoTopo.Background = brush('#A93232')
            self.TxtResultadoTitulo.Text = u'Processo concluído com problemas'
        elif cancelado:
            self.BrdResultadoTopo.Background = brush('#1F5FA8')
            self.TxtResultadoTitulo.Text = u'Processo cancelado'
        else:
            self.BrdResultadoTopo.Background = brush('#1E7B4A')
            self.TxtResultadoTitulo.Text = u'Processo concluído com sucesso'
        self.TxtResultadoMensagem.Text = u'\n'.join(linhas)
        self.problemas = [LinhaProblema(el, msg) for el, msg in problemas]
        vis = Visibility.Visible if self.problemas else Visibility.Collapsed
        self.PnlResultadoProblemas.Visibility = vis
        self.BtnResultadoTxt.Visibility = vis
        self.BtnResultadoTxt.Content = u'Salvar tabela em TXT…'
        self.BtnResultadoSelecionar.Content = u'Selecionar no Revit'
        self.BtnResultadoSelecionar.Visibility = (
            Visibility.Visible if any(p.eid is not None for p in self.problemas) else Visibility.Collapsed)
        self.TxtResultadoProblemas.Text = u'Não foi possível ({}):'.format(len(self.problemas))
        self.LvProblemas.ItemsSource = self.problemas
        self.PnlResultado.Visibility = Visibility.Visible
        self.BtnResultadoOk.Focus()

    def fechar_resultado(self):
        self.PnlResultado.Visibility = Visibility.Collapsed

    def selecionar_problemas(self, sender, e):
        linhas = list(self.LvProblemas.SelectedItems) or self.problemas
        ids = List[DB.ElementId]()
        for p in linhas:
            el = p.elemento()
            if el is not None:
                ids.Add(el.Id)
        uidoc.Selection.SetElementIds(ids)
        if ids.Count:
            self.BtnResultadoSelecionar.Content = u'{} selecionado(s) ✓'.format(ids.Count)
        else:
            self.BtnResultadoSelecionar.Content = u'Nada para selecionar no modelo'

    def salvar_problemas_txt(self, sender, e):
        nome = sanitize_filename(u'{} - problemas {}'.format(
            NOME_FERRAMENTA, datetime.datetime.now().strftime('%Y-%m-%d %H%M')))
        caminho = forms.save_file(file_ext='txt', default_name=nome, title=u'Salvar tabela de problemas')
        if not caminho:
            return
        limpar = lambda t: to_text(t).replace(u'\t', u' ').replace(u'\n', u' ')
        linhas = [NOME_FERRAMENTA, u'Projeto: ' + to_text(doc.Title),
                  u'Gerado em ' + datetime.datetime.now().strftime('%d/%m/%Y %H:%M'), u'',
                  u'Id\tElemento\tProblema']
        linhas += [u'{}\t{}\t{}'.format(p.Id, limpar(p.Elemento), limpar(p.Problema)) for p in self.problemas]
        try:
            with io.open(caminho, 'w', encoding='utf-8-sig', newline=u'\r\n') as f:
                f.write(u'\n'.join(linhas) + u'\n')
            self.BtnResultadoTxt.Content = u'TXT salvo ✓'
            self.BtnResultadoTxt.ToolTip = caminho
        except Exception as ex:
            forms.alert(u'Não foi possível salvar o TXT:\n{}\n\n{}'.format(caminho, ex), title=u'Erro')

    def ao_fechar(self, sender, e):
        if self.ocupado:
            forms.alert(u'Aguarde o fim da execução ou clique em Cancelar.', title=u'Aviso')
            e.Cancel = True


class SilenciarAvisos(DB.IFailuresPreprocessor):
    """Tira os avisos do Revit da tela durante a gravação e guarda o texto para o painel."""

    def __init__(self):
        self.avisos = []

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                self.avisos.append(to_text(f.GetDescriptionText()))
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def transacao(nome):
    """Transação com os avisos silenciados: devolve (transação, coletor de avisos)."""
    t = DB.Transaction(doc, nome)
    avisos = SilenciarAvisos()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(avisos)
    t.SetFailureHandlingOptions(opts)
    return t, avisos


# ==========================================================================
# 7. MAIN
# ==========================================================================
def main():
    if doc is None or doc.IsFamilyDocument:
        forms.alert(u'Abra um projeto do Revit (não uma família) para usar esta ferramenta.', exitscript=True)
    eletrodutos = coletar_eletrodutos()
    if not eletrodutos:
        forms.alert(u'Nenhum eletroduto encontrado no projeto ativo.', exitscript=True)
    limite = metros_para_interno(ALTURA_PADRAO)
    Janela([Item(el, limite) for el in eletrodutos]).ShowDialog()


if __name__ == '__main__':
    main()
