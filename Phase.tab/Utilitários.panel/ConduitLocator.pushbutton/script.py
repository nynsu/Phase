# -*- coding: utf-8 -*-
import time
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    UnitUtils, UnitTypeId, Element
)

inicio = time.time()
doc = revit.doc

# 1. Obter a altura da laje
input_altura = forms.ask_for_string(
    default="3.0",
    prompt="Insira a altura da laje (em metros):",
    title="Locação de Conduíte - ELETeam"
)

if not input_altura:
    script.exit()

try:
    limite_m = float(input_altura.replace(",", "."))
except ValueError:
    forms.alert("Valor inválido inserido.", title="Erro")
    script.exit()

# Converte metros -> unidades internas do Revit (pés)
limite_laje = UnitUtils.ConvertToInternalUnits(limite_m, UnitTypeId.Meters)

# Começa a contar o tempo do processamento (depois da digitação)
inicio = time.time()


# ---------- Funções auxiliares ----------
def get_nome_familia(tipo_elem):
    """Retorna 'Família + Tipo' como texto, de forma segura."""
    if tipo_elem is None:
        return u""
    partes = []
    try:
        partes.append(tipo_elem.FamilyName)
    except Exception:
        pass
    try:
        partes.append(Element.Name.GetValue(tipo_elem))
    except Exception:
        pass
    return u" ".join([u"{}".format(p) for p in partes if p])


def get_double(elem, bip, nome_pt):
    """Tenta pelo BuiltInParameter; se falhar, pelo nome do parâmetro."""
    p = elem.get_Parameter(bip)
    if p is None or not p.HasValue:
        lista = elem.GetParameters(nome_pt)
        p = lista[0] if lista else None
    if p is not None and p.HasValue:
        return p.AsDouble()
    return None


# 2. Coletar eletrodutos
eletrodutos = FilteredElementCollector(doc)\
                .OfCategory(BuiltInCategory.OST_Conduit)\
                .WhereElementIsNotElementType()\
                .ToElements()

if not eletrodutos:
    forms.alert("Nenhum eletroduto encontrado no projeto ativo!", title="Aviso")
    script.exit()

verificados = 0   # eletrodutos avaliados com sucesso
alterados = 0     # eletrodutos que realmente tiveram o valor gravado
erros_exemplo = ""

with revit.Transaction("Locação de Conduíte via pyRevit"):
    for conduit in eletrodutos:
        try:
            tipo_elem = doc.GetElement(conduit.GetTypeId())
            texto_familia = get_nome_familia(tipo_elem)

            # Elevação (em pés). Usa a elevação intermediária do eletroduto.
            z = get_double(conduit, BuiltInParameter.RBS_OFFSET_PARAM,
                           u"Elevação intermediária")
            if z is None:
                z_topo = get_double(conduit, BuiltInParameter.RBS_START_OFFSET_PARAM,
                                    u"Elevação da parte de cima da extremidade superior") or 0.0
                z_base = get_double(conduit, BuiltInParameter.RBS_END_OFFSET_PARAM,
                                    u"Elevação da parte de baixo da extremidade inferior") or 0.0
                z = (z_topo + z_base) / 2.0

            # Condicional
            if u"Rígido" in texto_familia or u"SEALTUBO" in texto_familia:
                resultado = u"SOBREPOSTO"
            elif z < 0:
                resultado = u"ENTERRADO"
            elif z > limite_laje:
                resultado = u"EMBUTIDO NA LAJE"
            else:
                resultado = u"EMBUTIDO NA PAREDE"

            param_instalacao = conduit.LookupParameter(u"Tipo de instalação")

            if param_instalacao:
                if not param_instalacao.IsReadOnly:
                    # Só grava se o valor for diferente do atual
                    if param_instalacao.AsString() != resultado:
                        param_instalacao.Set(resultado)
                        alterados += 1
                    verificados += 1
                else:
                    erros_exemplo = u"O parâmetro 'Tipo de instalação' é Somente Leitura."
            else:
                erros_exemplo = u"O parâmetro 'Tipo de instalação' não foi encontrado."

        except Exception as e:
            erros_exemplo = u"{}".format(e)

tempo = time.time() - inicio

if verificados > 0:
    forms.alert(
        u"Sucesso!\n{} eletrodutos verificados, {} alterados.\nTempo do script: {:.2f} s".format(
            verificados, alterados, tempo),
        title="ELETeam"
    )
else:
    forms.alert(
        u"Aviso: 0 eletrodutos atualizados.\nMotivo: " + (erros_exemplo or u"Parâmetros não encontrados."),
        title="ELETeam"
    )