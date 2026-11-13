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
"""Descrição da bomba - lista as bombas do projeto, edita os dados da bomba escolhida e
grava "Descrição do Material" da bomba e do quadro de comando (QCB), juntos ou um por vez.

Ambiente-alvo: Autodesk Revit 2026 + pyRevit (motor IronPython 2.7).
A interface (ui.xaml) é carregada por pyrevit.forms.WPFWindow.

Organização do arquivo:
    1. Importações e constantes
    2. Utilidades
    3. Dados do Revit
    4. Parâmetro "Descrição do Material"
    5. Janela: montagem e destino da gravação
    6. Janela: gravação e painel de resultado
    7. main()
"""

# ==========================================================================
# 1. IMPORTAÇÕES E CONSTANTES
# ==========================================================================
import io
import os
import re
import tempfile
import datetime

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

import System
from System.Collections.Generic import List
from System.Windows import FontStyles, Visibility
from System.Windows.Controls import Control
from System.Windows.Input import Key, Keyboard, ModifierKeys
from System.Windows.Media import Brushes, Color, SolidColorBrush

from pyrevit import revit, DB, forms, script

doc = revit.doc
uidoc = revit.uidoc

try:
    text_type = unicode  # noqa: F821  (IronPython 2.7)
except NameError:
    text_type = str

NOME_FERRAMENTA = u'Descrição da bomba'
XAML_FILE = script.get_bundle_file('ui.xaml')

PARAM_DESCRICAO = u'Descrição do Material'
PARAM_MARCA = u'Marca'
GRUPO_COMPARTILHADO = u'pyRevit_Bombas'
SEM_VALOR = u'—'             # parâmetro sem valor legível: bloqueia a gravação

PARAMETROS_BOMBA = [
    [u'Fase', u'Fase (padrão)'],
    [u'Tensão', u'Tensão (padrão)'],
    [u'Potência_HP', u'Potência_HP (padrão)'],
    [u'Vazão', u'Vazão (padrão)'],
    [u'MCA', u'MCA (padrão)'],
]
# chave, nomes do parâmetro, rótulo, casas decimais (Fase é tratada à parte)
DADOS = [
    ('Tensao', PARAMETROS_BOMBA[1], u'Tensão', None),
    ('Potencia', PARAMETROS_BOMBA[2], u'Potência', 1),
    ('Vazao', PARAMETROS_BOMBA[3], u'Vazão', None),
    ('MCA', PARAMETROS_BOMBA[4], u'MCA', None),
]
ROTULO_FASE = u'Fase'
INDICE_FASE = {u'1': 0, u'3': 1}    # Fase numérica <-> lista da janela


def brush(hexa):
    h = hexa.lstrip('#')
    return SolidColorBrush(Color.FromRgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))


COR_DICA = brush('#5A6778')
COR_ERRO = brush('#A93232')
BG_SEM_VALOR = brush('#FFF3CD')
BORDA_SEM_VALOR = brush('#E0A800')
BG_BLOQUEADO = brush('#EDEDED')


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
    """Ordenação natural: 'B-2' antes de 'B-10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', to_text(text))]


def valor_numero(texto):
    """Número digitado (aceita vírgula ou ponto). None se inválido."""
    try:
        return float(to_text(texto).strip().replace(u',', u'.'))
    except ValueError:
        return None


def sem_valor(texto):
    """Vazio, não lido ou zero: o campo fica destacado."""
    t = to_text(texto).strip()
    return t in (u'', SEM_VALOR, u'Indefinida') or valor_numero(t) == 0


def sanitize_filename(nome, padrao=u'Sem_nome'):
    limpo = re.sub(u'[\\\\/:*?"<>|]', u'_', to_text(nome)).strip(u' .')
    return limpo or padrao


def nome_elemento(el):
    """Nome legível: 'Família : Tipo' ou o nome do elemento."""
    try:
        tipo = doc.GetElement(el.GetTypeId())
        if tipo is not None and hasattr(tipo, 'FamilyName'):
            return u'{} : {}'.format(to_text(tipo.FamilyName), to_text(DB.Element.Name.GetValue(tipo)))
    except Exception:
        pass
    try:
        return to_text(DB.Element.Name.GetValue(el)) or to_text(el.Category.Name)
    except Exception:
        return u'—'


class LinhaProblema(object):
    """Linha da tabela de problemas. Guarda o id e o nome, não o objeto."""

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


# ==========================================================================
# 3. DADOS DO REVIT
# ==========================================================================
def localizar_dado(el, elem_type, nomes):
    """(parâmetro, está_no_tipo): instância antes do tipo, preferindo o que tem valor."""
    reserva = None
    for holder, no_tipo in ((el, False), (elem_type, True)):
        if not holder:
            continue
        for nome in nomes:
            p = holder.LookupParameter(nome)
            if p is None:
                continue
            if p.HasValue:
                return p, no_tipo
            if reserva is None:
                reserva = (p, no_tipo)
    return reserva or (None, False)


def valor_bruto(p):
    if p is None or not p.HasValue:
        return None
    if p.StorageType == DB.StorageType.Integer:
        return to_text(p.AsInteger())
    if p.StorageType == DB.StorageType.Double:
        return to_text(p.AsValueString() or p.AsDouble() or u'')
    if p.StorageType == DB.StorageType.String:
        return p.AsString() or u''
    return None


def escrever_valor(p, texto):
    """Grava o valor digitado conforme o tipo de dado do parâmetro. Chamar com transação aberta."""
    if p.StorageType == DB.StorageType.String:
        ok = p.Set(texto)
    elif p.StorageType == DB.StorageType.Integer:
        ok = p.Set(int(valor_numero(texto)))
    else:
        # SetValueString usa a unidade e o separador decimal do projeto
        virgula = doc.GetUnits().DecimalSymbol == DB.DecimalSymbol.Comma
        ajustado = texto.replace(u'.', u',') if virgula else texto.replace(u',', u'.')
        ok = p.SetValueString(ajustado)
        if not ok and p.Definition.GetDataType() == DB.SpecTypeId.Number:
            ok = p.Set(valor_numero(texto))
    if not ok:
        raise Exception(u'O Revit não aceitou o valor "{}".'.format(texto))


def format_number(val_str, decimals=None):
    """Limpa as unidades do Revit e formata o número com vírgula."""
    if not val_str:
        return SEM_VALOR
    match = re.search(r'[-+]?\d*[.,]?\d+', val_str)
    if not match:
        return val_str.strip()

    num = float(match.group(0).replace(u',', u'.'))
    if decimals is not None:
        return (u'{0:.' + to_text(decimals) + u'f}').format(num).replace(u'.', u',')

    if num.is_integer():
        return to_text(int(num))
    return u'{:.2f}'.format(num).rstrip(u'0').rstrip(u'.').replace(u'.', u',')


def texto_parametro(el, bip):
    p = el.get_Parameter(bip)
    return p.AsString().strip() if (p and p.AsString()) else u''


def coletar_quadros_projeto():
    """Quadros elétricos que começam com 'Quadro' (no nome do painel ou da família)."""
    coletor = DB.FilteredElementCollector(doc)\
        .OfCategory(DB.BuiltInCategory.OST_ElectricalEquipment)\
        .WhereElementIsNotElementType()\
        .ToElements()

    quadros = []
    for elem in coletor:
        nome = texto_parametro(elem, DB.BuiltInParameter.RBS_ELEC_PANEL_NAME)
        if not nome:
            p_alt = elem.LookupParameter(u'Nome do painel')
            nome = p_alt.AsString().strip() if (p_alt and p_alt.AsString()) else u''
        if not nome:
            nome = texto_parametro(elem, DB.BuiltInParameter.ALL_MODEL_MARK)

        elem_type = doc.GetElement(elem.GetTypeId())
        tipo_nome = u''
        fam_nome = u''
        if elem_type:
            try:
                tipo_nome = elem_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u''
            except Exception:
                tipo_nome = u''
            fam_nome = getattr(elem_type, 'FamilyName', u'') or u''

        if not (nome.lower().startswith(u'quadro') or fam_nome.strip().lower().startswith(u'quadro')):
            continue

        if nome:
            if fam_nome:
                exibir = (u'{} ({} - {})'.format(nome, fam_nome, tipo_nome) if tipo_nome
                          else u'{} ({})'.format(nome, fam_nome))
            else:
                exibir = nome
        else:
            detalhe = u'{} {}'.format(fam_nome, tipo_nome).strip()
            exibir = u'Quadro [Id: {}] {}'.format(eid_value(elem.Id), detalhe).strip()
        quadros.append((exibir, elem))

    quadros.sort(key=lambda q: q[0].lower())
    return quadros


def coletar_bombas():
    """Instâncias de família com "bomba" no nome. Os quadros (ex.: "Quadro de Comando de
    Bomba") ficam de fora: eles têm a lista própria."""
    bombas = []
    for el in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        try:
            familia = to_text(el.Symbol.Family.Name).strip().lower()
        except Exception:
            continue
        if u'bomba' in familia and not familia.startswith(u'quadro'):
            bombas.append((nome_elemento(el), el))
    bombas.sort(key=lambda b: natural_key(b[0]))
    return bombas


def contar_instancias_do_tipo(el):
    tipo_id = eid_value(el.GetTypeId())
    if el.Category is None:
        return 1
    coletor = DB.FilteredElementCollector(doc).OfCategoryId(el.Category.Id).WhereElementIsNotElementType()
    return len([x for x in coletor if eid_value(x.GetTypeId()) == tipo_id])


# ==========================================================================
# 4. PARÂMETRO "DESCRIÇÃO DO MATERIAL"
# ==========================================================================
def localizar_descricao(el, preferir_tipo):
    """(parâmetro, está_no_tipo). A bomba procura primeiro no tipo; o quadro, na instância."""
    tipo = doc.GetElement(el.GetTypeId())
    ordem = ((tipo, True), (el, False)) if preferir_tipo else ((el, False), (tipo, True))
    for holder, no_tipo in ordem:
        if holder is not None:
            p = holder.LookupParameter(PARAM_DESCRICAO)
            if p is not None:
                return p, no_tipo
    return None, False


def localizar_marca(el, preferir_tipo):
    """Localiza o parâmetro Marca no tipo ou na instância."""
    tipo = doc.GetElement(el.GetTypeId())
    ordem = ((tipo, True), (el, False)) if preferir_tipo else ((el, False), (tipo, True))
    for holder, no_tipo in ordem:
        if holder is not None:
            p = holder.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            if p is not None:
                return p, no_tipo
            p = holder.LookupParameter(PARAM_MARCA)
            if p is not None:
                return p, no_tipo
    return None, False


def criar_parametro_descricao(el):
    """Vincula o parâmetro de tipo à categoria do elemento. Chamar com transação aberta."""
    cat = el.Category
    if cat is None:
        raise Exception(u'O elemento não tem categoria.')

    app = doc.Application
    original_spf = app.SharedParameterFilename
    temp_spf = None
    try:
        if not original_spf or not os.path.exists(original_spf):
            temp_spf = os.path.join(tempfile.gettempdir(), 'pyrevit_temp_sp.txt')
            if not os.path.exists(temp_spf):
                with open(temp_spf, 'w') as f:
                    f.write('# Temporary shared parameter file\n')
            app.SharedParameterFilename = temp_spf

        def_file = app.OpenSharedParameterFile()
        if def_file is None:
            raise Exception(u'Não foi possível abrir o arquivo de parâmetros compartilhados.')

        grupo = def_file.Groups.Item[GRUPO_COMPARTILHADO]
        if grupo is None:
            grupo = def_file.Groups.Create(GRUPO_COMPARTILHADO)
        definicao = grupo.Definitions.Item[PARAM_DESCRICAO]
        if definicao is None:
            definicao = grupo.Definitions.Create(
                DB.ExternalDefinitionCreationOptions(PARAM_DESCRICAO, DB.SpecTypeId.String.Text))

        categorias = app.Create.NewCategorySet()
        categorias.Insert(cat)
        vinculos = doc.ParameterBindings
        if vinculos.Contains(definicao):
            existente = vinculos.Item[definicao]
            if hasattr(existente, 'Categories'):
                for c in existente.Categories:
                    if not categorias.Contains(c):
                        categorias.Insert(c)
            ok = vinculos.ReInsert(definicao, app.Create.NewTypeBinding(categorias), DB.GroupTypeId.IdentityData)
        else:
            ok = vinculos.Insert(definicao, app.Create.NewTypeBinding(categorias), DB.GroupTypeId.IdentityData)
        if not ok:
            raise Exception(u'O Revit não aceitou criar o parâmetro "{}".'.format(PARAM_DESCRICAO))
    finally:
        if temp_spf is not None:      # devolve a configuração do Revit como estava
            try:
                app.SharedParameterFilename = original_spf or u''
            except Exception:
                pass


class SilenciarAvisos(DB.IFailuresPreprocessor):
    """Tira os avisos do Revit da tela e guarda o texto para o painel de resultado."""

    def __init__(self):
        self.avisos = []

    def PreprocessFailures(self, accessor):
        for f in accessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                self.avisos.append(to_text(f.GetDescriptionText()))
                accessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def erro_amigavel(ex):
    """Troca mensagens técnicas da API do Revit por texto claro."""
    bruto = to_text(ex)
    baixo = bruto.lower()
    regras = (
        (u'outside of transaction', u'Falha interna ao gravar (fora de transação). Tente novamente.'),
        (u'read-only', u'O parâmetro é somente leitura e não pode ser alterado.'),
        (u'somente leitura', u'O parâmetro é somente leitura e não pode ser alterado.'),
        (u'not valid', u'O elemento não existe mais ou não é válido. Selecione-o de novo.'),
        (u'has been deleted', u'O elemento foi excluído do projeto.'),
        (u'cannot be modified', u'O Revit não permite alterar este elemento (pode estar em um grupo ou bloqueado).'),
        (u'worksharing', u'O elemento está sendo editado por outra pessoa (compartilhamento de trabalho).'),
        (u'owned by', u'O elemento está sendo editado por outra pessoa (compartilhamento de trabalho).'),
        (u'storage type', u'O valor não combina com o tipo do parâmetro (texto ou número).'),
    )
    for chave, texto in regras:
        if chave in baixo:
            return texto
    return bruto


def transacao(nome):
    t = DB.Transaction(doc, nome)
    avisos = SilenciarAvisos()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(avisos)
    t.SetFailureHandlingOptions(opts)
    return t, avisos


def gravar_marca(el, texto, preferir_tipo, nome):
    """Grava o parâmetro Marca no elemento/tipo."""
    if not texto:
        return []
    p, _ = localizar_marca(el, preferir_tipo)
    if p is None:
        raise Exception(u'O parâmetro "Marca" não foi encontrado em {}.'.format(nome))
    if p.IsReadOnly:
        raise Exception(u'O parâmetro "Marca" em {} é somente leitura.'.format(nome))
    if not p.Set(texto):
        raise Exception(u'O Revit não aceitou o texto da Marca em {}.'.format(nome))
    return []


def gravar_descricao(el, texto, preferir_tipo, nome, marca=None):
    """Grava num elemento, em transação própria: a falha de um não desfaz o outro.
    Devolve a lista de avisos do Revit; levanta exceção se não gravou."""
    t, avisos = transacao(u'{} - {}'.format(NOME_FERRAMENTA, nome))
    t.Start()
    try:
        p, _ = localizar_descricao(el, preferir_tipo)
        if p is None:
            criar_parametro_descricao(el)
            doc.Regenerate()
            p, _ = localizar_descricao(el, preferir_tipo)
        if p is None:
            raise Exception(u'O parâmetro "{}" não foi encontrado.'.format(PARAM_DESCRICAO))
        if p.IsReadOnly:
            raise Exception(u'O parâmetro "{}" é somente leitura.'.format(PARAM_DESCRICAO))
        if not p.Set(texto):
            raise Exception(u'O Revit não aceitou o texto.')
        if marca:        # Marca na mesma transação (precisa estar com a transação aberta)
            gravar_marca(el, marca, preferir_tipo, nome)
        if t.Commit() != DB.TransactionStatus.Committed:
            raise Exception(u'O Revit não confirmou a gravação.')
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return sorted(set(avisos.avisos))


def gravar_dados(alteracoes):
    """Grava os dados da bomba numa transação só. alteracoes: [(parâmetro, rótulo, texto), ...]"""
    t, avisos = transacao(u'{} - Dados'.format(NOME_FERRAMENTA))
    t.Start()
    try:
        for p, rotulo, texto in alteracoes:
            try:
                escrever_valor(p, texto)
            except Exception as ex:
                raise Exception(u'{}: {}'.format(rotulo, ex))
        if t.Commit() != DB.TransactionStatus.Committed:
            raise Exception(u'O Revit não confirmou a gravação.')
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return sorted(set(avisos.avisos))


def ponto_elemento(el):
    """Retorna o XYZ de localização do elemento (LocationPoint ou centro da BoundingBox)."""
    if el is None:
        return None
    loc = getattr(el, 'Location', None)
    if loc is not None and hasattr(loc, 'Point'):
        return loc.Point
    try:
        bbox = el.get_BoundingBox(None)
        if bbox is not None:
            return (bbox.Min + bbox.Max) * 0.5
    except Exception:
        pass
    return None


def calcular_distancia_metros(el1, el2):
    """Retorna a distância euclidiana em metros entre dois elementos (1 pé = 0.3048 m)."""
    p1 = ponto_elemento(el1)
    p2 = ponto_elemento(el2)
    if p1 is None or p2 is None:
        return None
    dist_pes = p1.DistanceTo(p2)
    return dist_pes * 0.3048


# ==========================================================================
# 5. JANELA: MONTAGEM E DESTINO DA GRAVAÇÃO
# ==========================================================================
class Janela(forms.WPFWindow):

    def __init__(self, bombas, inicial):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.rotulos = [b[0] for b in bombas]
        self.bombas = [b[1] for b in bombas]
        self.indice = inicial
        self.carregando = True
        self.trocando = False
        self.problemas = []
        self.gerado_bomba = None
        self.gerado_quadro = None
        self.campos = {'Tensao': self.TxtTensao, 'Potencia': self.TxtPotencia,
                       'Vazao': self.TxtVazao, 'MCA': self.TxtMCA}

        self.TxtContexto.Text = u'Projeto ativo: {}'.format(doc.Title)
        self.TxtBombasTotal.Text = (u'1 bomba no projeto' if len(self.bombas) == 1
                                     else u'{} bombas no projeto'.format(len(self.bombas)))
        self.CmbBomba.ItemsSource = self.rotulos
        self.CmbBomba.SelectedIndex = inicial
        self.definir_bomba(inicial)
        self.carregar_quadros()

        self.CmbBomba.SelectionChanged += self.bomba_mudou
        self.CmbFase.SelectionChanged += self.dado_mudou
        self.TxtFase.TextChanged += self.dado_mudou
        for ctrl in self.campos.values():
            ctrl.TextChanged += self.dado_mudou
        self.CmbQuadros.SelectionChanged += lambda s, e: self.atualizar_quadro()
        self.BtnGravarDados.Click += lambda s, e: self.executar(['dados'])
        self.BtnGravarBomba.Click += lambda s, e: self.executar(['bomba'])
        self.BtnGravarQuadro.Click += lambda s, e: self.executar(['quadro'])
        self.BtnGravarTodos.Click += self.fazer_selecionados
        self.BtnCancelar.Click += lambda s, e: self.Close()
        self.CmbQuadroMarca.SelectionChanged += self.quadro_marca_mudou
        self.BtnResultadoOk.Click += lambda s, e: self.fechar_resultado()
        self.BtnResultadoSelecionar.Click += self.selecionar_problemas
        self.BtnResultadoTxt.Click += self.salvar_problemas_txt
        self.Closing += self.ao_fechar
        self.PreviewKeyDown += self.tecla_atalho

    # ---------------------------------------------------------------- bomba e dados
    def definir_bomba(self, indice):
        self.element = self.bombas[indice]
        self.elem_type = doc.GetElement(self.element.GetTypeId())
        self.ler_dados()
        self.gerar_textos(forcar=True)
        self.atualizar_bomba()
        if getattr(self, 'quadros', None):
            self.atualizar_quadro()

    def bomba_mudou(self, sender, e):
        if self.trocando:
            return
        novo = self.CmbBomba.SelectedIndex
        if novo < 0 or novo == self.indice:
            return
        if self.alteracoes() and not forms.alert(
                u'Os dados editados desta bomba ainda não foram gravados. Trocar de bomba e descartar?',
                title=u'Dados não gravados', yes=True, no=True):
            self.trocando = True
            self.CmbBomba.SelectedIndex = self.indice
            self.trocando = False
            return
        self.indice = novo
        self.definir_bomba(novo)

    def ler_dados(self):
        """Preenche os campos com o que está na bomba."""
        self.carregando = True
        self.info = {}
        el, tipo = self.element, self.elem_type

        p, no_tipo = localizar_dado(el, tipo, PARAMETROS_BOMBA[0])
        self.info['Fase'] = (p, no_tipo, ROTULO_FASE)
        raw = (valor_bruto(p) or u'').strip()
        self.fase_lista = p is not None and not p.IsReadOnly and p.StorageType == DB.StorageType.Integer
        self.CmbFase.Visibility = Visibility.Visible if self.fase_lista else Visibility.Collapsed
        self.TxtFase.Visibility = Visibility.Collapsed if self.fase_lista else Visibility.Visible
        if self.fase_lista:
            self.CmbFase.SelectedIndex = INDICE_FASE.get(raw, -1)
        else:
            self.TxtFase.Text = self.nome_fase(raw) if raw else SEM_VALOR
            self.preparar_campo(self.TxtFase, p)

        for chave, nomes, rotulo, casas in DADOS:
            p, no_tipo = localizar_dado(el, tipo, nomes)
            self.info[chave] = (p, no_tipo, rotulo)
            self.campos[chave].Text = format_number(valor_bruto(p), casas)
            self.preparar_campo(self.campos[chave], p)

        self.originais = dict((chave, self.valor_campo(chave)) for chave in self.info)
        self.carregando = False
        self.atualizar_campos()
        self.mostrar_destino_dados()

    def motivo_bloqueio(self, p):
        if p is None:
            return u'Este parâmetro não existe nesta bomba.'
        if p.IsReadOnly:
            return u'Somente leitura: o valor vem de fórmula ou da própria família.'
        return None

    def preparar_campo(self, ctrl, p):
        """Campo que o Revit não deixa alterar: cinza, itálico e com o motivo."""
        motivo = self.motivo_bloqueio(p)
        ctrl.IsReadOnly = motivo is not None
        ctrl.FontStyle = FontStyles.Italic if motivo else FontStyles.Normal
        ctrl.ToolTip = motivo

    def valor_campo(self, chave):
        if chave == 'Fase':
            if self.fase_lista:
                i = self.CmbFase.SelectedIndex
                return (u'1', u'3')[i] if i in (0, 1) else u''   # lista: Monofásica, Trifásico
            return to_text(self.TxtFase.Text).strip()
        return to_text(self.campos[chave].Text).strip()

    def alteracoes(self):
        """[(parâmetro, rótulo, antes, depois)] só do que o projetista mudou."""
        saida = []
        for chave in ['Fase'] + [d[0] for d in DADOS]:
            p, _, rotulo = self.info[chave]
            depois = self.valor_campo(chave)
            if p is not None and not p.IsReadOnly and depois != self.originais[chave]:
                saida.append((p, rotulo, self.originais[chave], depois))
        return saida

    def dado_mudou(self, sender, e):
        if not self.carregando:
            self.atualizar_campos()

    def atualizar_campos(self):
        """Destaca o que está sem valor e refaz os textos automáticos."""
        algum = False
        for chave, ctrl in self.campos.items():
            vazio = sem_valor(self.valor_campo(chave))
            algum = algum or vazio
            self.pintar(ctrl, vazio, self.motivo_bloqueio(self.info[chave][0]) is not None)
        fase_vazia = sem_valor(self.valor_campo('Fase'))
        algum = algum or fase_vazia
        self.BrdFase.BorderBrush = BORDA_SEM_VALOR if fase_vazia else Brushes.Transparent
        if not self.fase_lista:
            self.pintar(self.TxtFase, fase_vazia, self.motivo_bloqueio(self.info['Fase'][0]) is not None)
        self.BrdSemValor.Visibility = Visibility.Visible if algum else Visibility.Collapsed
        self.gerar_textos()
        quadro = self.quadro_atual()
        if quadro is not None:
            marca_atual = self.valor_marca_quadro()
            if not marca_atual or marca_atual in (u'REF.: EBARA BMS', u'REF.: EBARA STS'):
                self.selecionar_marca_quadro(self.marca_sugerida_quadro())

    def pintar(self, ctrl, vazio, bloqueado):
        if vazio:
            ctrl.Background = BG_SEM_VALOR
            ctrl.BorderBrush = BORDA_SEM_VALOR
        elif bloqueado:
            ctrl.Background = BG_BLOQUEADO
            ctrl.ClearValue(Control.BorderBrushProperty)
        else:      # devolve ao estilo (borda azul com foco)
            ctrl.ClearValue(Control.BackgroundProperty)
            ctrl.ClearValue(Control.BorderBrushProperty)

    def mostrar_destino_dados(self):
        no_tipo = [info[2] for info in self.info.values() if info[0] is not None and info[1]]
        if no_tipo:
            self.TxtDadosDestino.Text = u'{} no tipo: vale para {} bomba(s) deste tipo. O resto só nesta bomba.'.format(
                u', '.join(sorted(no_tipo)), contar_instancias_do_tipo(self.element))
        else:
            self.TxtDadosDestino.Text = u'Grava na instância: vale só para esta bomba.'

    def nome_fase(self, raw):
        if raw == u'3':
            return u'Trifásico'
        if raw == u'1':
            return u'Monofásica'
        return raw or u'Indefinida'

    def fase_do_quadro(self, raw, fase):
        if u'3' in raw or u'tri' in fase.lower():
            return u'Trifásico'
        if u'1' in raw or u'mono' in fase.lower():
            return u'Monofásico'
        if u'2' in raw or u'bi' in fase.lower():
            return u'Bifásico'
        return fase

    def info_distancia_quadro(self):
        """Retorna (dist_metros, eh_distante) ou (None, False)."""
        quadro = self.quadro_atual()
        el_bomba = getattr(self, 'element', None)
        if quadro is None or el_bomba is None:
            return None, False
        d = calcular_distancia_metros(el_bomba, quadro)
        if d is None:
            return None, False
        return d, (d > 200.0)

    def gerar_textos(self, forcar=False):
        """Refaz as descrições; o texto editado à mão só é trocado ao mudar de bomba."""
        raw = self.valor_campo('Fase')
        if raw == SEM_VALOR:
            raw = u''
        fase = self.nome_fase(raw)
        tensao = self.valor_campo('Tensao') or SEM_VALOR
        potencia = self.valor_campo('Potencia') or SEM_VALOR
        vazao = self.valor_campo('Vazao') or SEM_VALOR
        mca = self.valor_campo('MCA') or SEM_VALOR
        bomba = u'Motobomba Centrífuga Submersa, {}, {} V, {} CV, {} m³/h @ {} mca'.format(
            fase, tensao, potencia, vazao, mca)

        fase_q = self.fase_do_quadro(raw, fase)
        dist_m, eh_distante = self.info_distancia_quadro()
        sufixo_rele = u' com Relé de Subcarga' if eh_distante else u' com Relé de Nível'

        quadro = u'Quadro de Comando de Bomba {} CV {} V {}{}'.format(
            potencia, tensao, fase_q, sufixo_rele)

        if forcar or self.TxtBombaNovo.Text == self.gerado_bomba:
            self.TxtBombaNovo.Text = bomba
        if forcar or self.TxtQuadroNovo.Text == self.gerado_quadro:
            self.TxtQuadroNovo.Text = quadro
        self.gerado_bomba = bomba
        self.gerado_quadro = quadro

    def carregar_quadros(self):
        lista = coletar_quadros_projeto()
        self.quadros = [q[1] for q in lista]
        self.CmbQuadros.ItemsSource = [q[0] for q in lista]
        if not lista:
            motivo = u'Nenhum quadro compatível no projeto.'
            for ctrl in (self.CmbQuadros, self.TxtQuadroNovo, self.BtnGravarQuadro,
                         self.CmbQuadroMarca):
                ctrl.IsEnabled = False
            self.TxtQuadroDestino.Visibility = Visibility.Visible   # aviso importante
            self.BtnGravarTodos.Content = u'Gravar Todos (Dados + Bomba)'
            self.TxtQuadroAtual.Text = SEM_VALOR
            self.TxtQuadroDestino.Text = motivo
            return
        indice = 0
        for i, (nome, _) in enumerate(lista):    # prefere QCB ou Bomba no nome
            if u'qcb' in nome.lower() or u'bomba' in nome.lower():
                indice = i
                break
        self.CmbQuadros.SelectedIndex = indice
        self.atualizar_quadro()

    def quadro_atual(self):
        i = self.CmbQuadros.SelectedIndex
        return self.quadros[i] if 0 <= i < len(self.quadros) else None

    def mostrar_destino(self, el, preferir_tipo, txt_atual, txt_destino, palavra):
        """Valor atual e onde a gravação vai cair. Diz antes o que não vai funcionar."""
        p, no_tipo = localizar_descricao(el, preferir_tipo)
        txt_destino.Foreground = COR_DICA
        if p is None:
            txt_atual.Text = u'(parâmetro não existe)'
            txt_destino.Text = u'O parâmetro será criado como tipo ao gravar.'
            return
        txt_atual.Text = to_text(p.AsString()) or u'(vazio)'
        if p.IsReadOnly:
            txt_destino.Foreground = COR_ERRO
            txt_destino.Text = u'O parâmetro é somente leitura: não dá para gravar.'
        elif no_tipo:
            txt_destino.Text = u'Grava no tipo: vale para {} {} deste tipo.'.format(
                contar_instancias_do_tipo(el), palavra)
        else:
            txt_destino.Text = u'Grava na instância: vale só para este elemento.'

    def quadro_marca_mudou(self, sender, e):
        sel = self.CmbQuadroMarca.SelectedItem
        txt_sel = to_text(sel.Content if hasattr(sel, 'Content') else sel)
        if txt_sel == u'Outro':
            self.TxtQuadroMarcaOutro.Visibility = Visibility.Visible
        else:
            self.TxtQuadroMarcaOutro.Visibility = Visibility.Collapsed

    def valor_marca_quadro(self):
        sel = self.CmbQuadroMarca.SelectedItem
        if sel is None:
            return u''
        txt_sel = to_text(sel.Content if hasattr(sel, 'Content') else sel).strip()
        if txt_sel == u'Outro':
            return to_text(self.TxtQuadroMarcaOutro.Text).strip()
        return txt_sel

    def atualizar_bomba(self):
        self.mostrar_destino(self.element, True, self.TxtBombaAtual, self.TxtBombaDestino, u'bomba(s)')
        p_marca, _ = localizar_marca(self.element, True)
        if p_marca is not None:
            val = to_text(p_marca.AsString()).strip()
            self.TxtBombaMarca.Text = val

    def marca_sugerida_quadro(self):
        """Retorna 'REF.: EBARA BMS' se monofásica ou 'REF.: EBARA STS' se trifásica."""
        raw = self.valor_campo('Fase')
        fase = self.nome_fase(raw)
        fase_q = self.fase_do_quadro(raw, fase)
        if u'tri' in fase_q.lower():
            return u'REF.: EBARA STS'
        return u'REF.: EBARA BMS'

    def selecionar_marca_quadro(self, valor):
        opcoes = [u'REF.: EBARA BMS', u'REF.: EBARA STS', u'REF.: EBARA APS']
        if valor in opcoes:
            for item in self.CmbQuadroMarca.Items:
                c = to_text(item.Content if hasattr(item, 'Content') else item)
                if c == valor:
                    self.CmbQuadroMarca.SelectedItem = item
                    break
            self.TxtQuadroMarcaOutro.Visibility = Visibility.Collapsed
            self.TxtQuadroMarcaOutro.Text = u''
        elif valor:
            for item in self.CmbQuadroMarca.Items:
                c = to_text(item.Content if hasattr(item, 'Content') else item)
                if c == u'Outro':
                    self.CmbQuadroMarca.SelectedItem = item
                    break
            self.TxtQuadroMarcaOutro.Visibility = Visibility.Visible
            self.TxtQuadroMarcaOutro.Text = valor
        else:
            sugerida = self.marca_sugerida_quadro()
            self.selecionar_marca_quadro(sugerida)

    def atualizar_quadro(self):
        quadro = self.quadro_atual()
        if quadro is not None:
            self.mostrar_destino(quadro, False, self.TxtQuadroAtual, self.TxtQuadroDestino, u'quadro(s)')
            
            # Exibir distância
            dist_m, eh_distante = self.info_distancia_quadro()
            if dist_m is not None:
                classif = u'Distante (> 200m) → Relé de Subcarga' if eh_distante else u'Próxima (≤ 200m) → Relé de Nível'
                self.TxtDistanciaQuadro.Text = u'Distância da bomba: {:.1f} m ({})'.format(dist_m, classif)
                self.TxtDistanciaQuadro.Visibility = Visibility.Visible
            else:
                self.TxtDistanciaQuadro.Text = u'Distância não calculada (adotado: Próxima)'
                self.TxtDistanciaQuadro.Visibility = Visibility.Visible

            p_marca, _ = localizar_marca(quadro, False)
            val = to_text(p_marca.AsString()).strip() if p_marca else u''
            if not val or val in (u'REF.: EBARA BMS', u'REF.: EBARA STS'):
                self.selecionar_marca_quadro(self.marca_sugerida_quadro())
            else:
                self.selecionar_marca_quadro(val)
            self.gerar_textos()

    # ==========================================================================
    # 6. JANELA: GRAVAÇÃO E PAINEL DE RESULTADO
    # ==========================================================================
    def tecla_atalho(self, sender, e):
        """Esc: fecha o resultado ou cancela. Ctrl+Enter: Gravar Todos."""
        if e.Key == Key.Escape:
            e.Handled = True
            if self.PnlResultado.Visibility == Visibility.Visible:
                self.fechar_resultado()
            else:
                self.Close()
        elif (e.Key == Key.Enter and (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control
              and self.PnlResultado.Visibility != Visibility.Visible):
            e.Handled = True
            self.fazer_selecionados(sender, e)

    def fazer_selecionados(self, sender, e):
        funcoes = ['dados', 'bomba']
        if self.quadros:   # sem quadro compativel no projeto, grava so dados e bomba
            funcoes.append('quadro')
        self.executar(funcoes)

    def dados_funcao(self, funcao):
        """(elemento, texto, preferir_tipo, nome para mostrar)"""
        if funcao == 'bomba':
            return self.element, to_text(self.TxtBombaNovo.Text).strip(), True, u'Bomba'
        quadro = self.quadro_atual()
        return quadro, to_text(self.TxtQuadroNovo.Text).strip(), False, u'Quadro'

    def validar_dados(self):
        for p, rotulo, _, depois in self.alteracoes():
            if not depois or depois == SEM_VALOR:
                return u'Preencha {} antes de gravar os dados.'.format(rotulo)
            numerico = p.StorageType != DB.StorageType.String
            if numerico and valor_numero(depois) is None:
                return u'{}: digite só o número, como 2,5.'.format(rotulo)
        return None

    def validar(self, funcoes):
        for funcao in funcoes:
            if funcao == 'dados':
                erro = self.validar_dados()
                if erro:
                    return erro
                if funcoes == ['dados'] and not self.alteracoes():
                    return u'Nenhum dado foi alterado.'
                continue
            el, texto, _, nome = self.dados_funcao(funcao)
            if el is None:
                return u'Escolha um quadro na lista de quadros.'
            if not texto:
                return u'A descrição de {} está vazia.'.format(nome.lower())
            if SEM_VALOR in texto:
                return (u'A descrição de {} tem um valor que não pôde ser lido (—). '
                        u'Preencha o dado ou ajuste o texto antes de gravar.').format(nome.lower())
        return None

    def executar(self, funcoes):
        erro = self.validar(funcoes)
        if erro:
            forms.alert(erro, title=u'Aviso')
            return
        mudancas = self.alteracoes() if 'dados' in funcoes else []
        partes = []
        if mudancas:
            nomes = [u'{}: {} → {}'.format(m[1], m[2], m[3]) for m in mudancas]
            partes.append(u'Dados da Bomba:\n{}'.format(u'\n'.join(nomes)))
        for funcao in funcoes:
            if funcao == 'dados':
                continue
            el, texto, preferir_tipo, nome = self.dados_funcao(funcao)
            p, _ = localizar_descricao(el, preferir_tipo)
            criar = u'\n(o parâmetro será criado como tipo)' if p is None else u''
            partes.append(u'{}:\n{}{}'.format(nome, texto, criar))
        if not partes:
            forms.alert(u'Nenhum dado foi alterado.', title=u'Aviso')
            return
        if not forms.alert(u'Gravar?\n\n{}'.format(u'\n\n'.join(partes)),
                           title=u'Confirmar', yes=True, no=True):
            return

        linhas = []
        problemas = []
        gravou = False
        dados_ok = False
        tg = DB.TransactionGroup(doc, NOME_FERRAMENTA)
        tg.Start()
        try:
            for funcao in funcoes:
                if funcao == 'dados':
                    if not mudancas:
                        linhas.append(u'Dados: nenhum valor alterado.')
                        continue
                    try:
                        avisos = gravar_dados([(m[0], m[1], m[3]) for m in mudancas])
                        gravou = dados_ok = True
                        linhas.append(u'Dados: {} valor(es) gravado(s).'.format(len(mudancas)))
                        problemas += [(self.element, u'Aviso do Revit: ' + a) for a in avisos]
                    except Exception as ex:
                        linhas.append(u'Dados: NÃO foram gravados. Veja o motivo na tabela.')
                        problemas.append((self.element, erro_amigavel(ex)))
                        # As descrições dependem dos dados: sem eles, nada mais é gravado
                        for resto in funcoes[funcoes.index('dados') + 1:]:
                            nome = u'Bomba' if resto == 'bomba' else u'Quadro'
                            linhas.append(u'{}: não gravado, porque os Dados falharam.'.format(nome))
                        break
                    continue
                el, texto, preferir_tipo, nome = self.dados_funcao(funcao)
                rotulo = nome if funcao == 'bomba' else u'Quadro {}'.format(to_text(self.CmbQuadros.SelectedItem))
                try:
                    texto_marca = to_text(self.TxtBombaMarca.Text).strip() if funcao == 'bomba' else self.valor_marca_quadro()
                    avisos = gravar_descricao(el, texto, preferir_tipo, nome, texto_marca)
                    gravou = True
                    msg_grav = u'{}: descrição gravada.'.format(rotulo)
                    if texto_marca:
                        msg_grav = u'{}: descrição e marca gravadas.'.format(rotulo)
                    linhas.append(msg_grav)
                    for aviso in avisos:
                        problemas.append((el, u'Aviso do Revit: ' + aviso))
                except Exception as ex:
                    linhas.append(u'{}: NÃO foi gravado. Veja o motivo na tabela.'.format(rotulo))
                    problemas.append((el, erro_amigavel(ex)))
            if gravou:
                tg.Assimilate()       # vira um único passo no Ctrl+Z do Revit
            else:
                tg.RollBack()
        except Exception as ex:
            if tg.HasStarted() and not tg.HasEnded():
                tg.RollBack()
            dados_ok = False
            linhas = [u'Nada foi gravado.']
            problemas.append((None, u'Erro geral: {}'.format(ex)))

        if dados_ok:
            self.ler_dados()
        self.atualizar_bomba()
        self.atualizar_quadro()
        self.mostrar_resultado(linhas, problemas)

    def mostrar_resultado(self, linhas, problemas=None):
        """Painel por cima da janela. problemas: [(elemento ou None, u'motivo'), ...]"""
        problemas = problemas or []
        if problemas:
            self.BrdResultadoTopo.Background = brush('#A93232')
            self.TxtResultadoTitulo.Text = u'Processo concluído com problemas'
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
        if self.alteracoes() and not forms.alert(
                u'Os dados editados ainda não foram gravados. Fechar mesmo assim?',
                title=u'Dados não gravados', yes=True, no=True):
            e.Cancel = True


# ==========================================================================
# 7. MAIN
# ==========================================================================
def main():
    if doc is None or doc.IsFamilyDocument:
        forms.alert(u'Abra um projeto do Revit (não uma família) para usar esta ferramenta.', exitscript=True)

    bombas = coletar_bombas()
    if not bombas:
        forms.alert(u'Nenhuma família com "bomba" no nome foi encontrada no projeto.',
                    title=u'Atenção', exitscript=True)

    # Se já houver uma bomba selecionada no modelo, a janela abre nela
    selecionados = set(eid_value(el.Id) for el in revit.get_selection().elements)
    inicial = 0
    for i, (_, el) in enumerate(bombas):
        if eid_value(el.Id) in selecionados:
            inicial = i
            break
    Janela(bombas, inicial).ShowDialog()


if __name__ == '__main__':
    main()
