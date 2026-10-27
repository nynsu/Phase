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

import os
import re
import tempfile
from pyrevit import revit, DB, forms

try:
    from System.Windows import Visibility
except Exception:
    Visibility = None

doc = revit.doc


def get_raw_value(el, elem_type, param_names):
    """Procura o valor do parâmetro na Instância ou no Tipo da bomba."""
    for holder in (el, elem_type):
        if not holder:
            continue
        for name in param_names:
            p = holder.LookupParameter(name)
            if p and p.HasValue:
                if p.StorageType == DB.StorageType.Integer:
                    return str(p.AsInteger())
                elif p.StorageType == DB.StorageType.Double:
                    return str(p.AsValueString() or p.AsDouble() or "")
                elif p.StorageType == DB.StorageType.String:
                    return p.AsString() or ""
    return None


def format_number(val_str, decimals=None):
    """Limpa as unidades do Revit e formata o número com vírgula."""
    if not val_str:
        return "0"
    match = re.search(r"[-+]?\d*[.,]?\d+", val_str)
    if not match:
        return val_str.strip()

    num = float(match.group(0).replace(",", "."))
    if decimals is not None:
        return ("{0:." + str(decimals) + "f}").format(num).replace(".", ",")

    if num.is_integer():
        return str(int(num))
    return "{:.2f}".format(num).rstrip("0").rstrip(".").replace(".", ",")


def garantir_parametro_descricao(element):
    """Pergunta se deseja criar o parâmetro 'Descrição do Material' caso ele não exista."""
    elem_type = doc.GetElement(element.GetTypeId())
    
    # Verifica se já existe na instância ou no tipo
    if (element.LookupParameter("Descrição do Material") is not None) or \
       (elem_type and elem_type.LookupParameter("Descrição do Material") is not None):
        return True

    # Pergunta ao utilizador se deseja criar automaticamente
    cat_nome = element.Category.Name if element.Category else u"esta categoria"
    resposta = forms.alert(
        u"O parâmetro 'Descrição do Material' não foi encontrado para {}.\n\nDeseja criá-lo automaticamente agora como Parâmetro de Tipo?".format(cat_nome),
        title=u"Criar Parâmetro Automático",
        yes=True,
        no=True
    )

    if not resposta:
        return False

    app = doc.Application
    original_spf = app.SharedParameterFilename
    temp_spf = None

    try:
        if not original_spf or not os.path.exists(original_spf):
            temp_dir = tempfile.gettempdir()
            temp_spf = os.path.join(temp_dir, "pyrevit_temp_sp.txt")
            if not os.path.exists(temp_spf):
                with open(temp_spf, "w") as f:
                    f.write("# Temporary shared parameter file\n")
            app.SharedParameterFilename = temp_spf

        def_file = app.OpenSharedParameterFile()
        if not def_file:
            forms.alert(u"Não foi possível abrir o arquivo de parâmetros partilhados.", title=u"Erro")
            return False

        group = def_file.Groups.Item["pyRevit_Bombas"]
        if not group:
            group = def_file.Groups.Create("pyRevit_Bombas")

        definition = group.Definitions.Item["Descrição do Material"]
        if not definition:
            try:
                opt = DB.ExternalDefinitionCreationOptions("Descrição do Material", DB.SpecTypeId.String.Text)
                definition = group.Definitions.Create(opt)
            except:
                opt = DB.ExternalDefinitionCreationOptions("Descrição do Material", DB.ParameterType.Text)
                definition = group.Definitions.Create(opt)

        cat = element.Category
        if not cat:
            return False

        cat_set = app.Create.NewCategorySet()
        cat_set.Insert(cat)

        try:
            group_type = DB.GroupTypeId.IdentityData
        except:
            group_type = DB.BuiltInParameterGroup.PG_IDENTITY_DATA

        with revit.Transaction(u"Criar Parâmetro de Projeto: Descrição do Material"):
            binding_map = doc.ParameterBindings
            if binding_map.Contains(definition):
                existing_binding = binding_map.Item[definition]
                if hasattr(existing_binding, "Categories"):
                    for existing_cat in existing_binding.Categories:
                        if not cat_set.Contains(existing_cat):
                            cat_set.Insert(existing_cat)
                type_binding = app.Create.NewTypeBinding(cat_set)
                binding_map.ReInsert(definition, type_binding, group_type)
            else:
                type_binding = app.Create.NewTypeBinding(cat_set)
                binding_map.Insert(definition, type_binding, group_type)

        forms.alert(u"Parâmetro 'Descrição do Material' criado com sucesso para {}!".format(cat_nome), title=u"Sucesso")
        return True

    except Exception as e:
        forms.alert(u"Erro ao criar parâmetro automático:\n{}".format(str(e)), title=u"Erro")
        return False
    finally:
        if temp_spf and original_spf:
            try:
                app.SharedParameterFilename = original_spf
            except:
                pass


def coletar_quadros_projeto(doc):
    """Coleta e formata todos os quadros elétricos do projeto que comecem com 'Quadro'."""
    collector = DB.FilteredElementCollector(doc)\
        .OfCategory(DB.BuiltInCategory.OST_ElectricalEquipment)\
        .WhereElementIsNotElementType()\
        .ToElements()

    quadros = []
    for elem in collector:
        # Obter nome do painel
        p_nome = elem.get_Parameter(DB.BuiltInParameter.RBS_ELEC_PANEL_NAME)
        nome = p_nome.AsString().strip() if (p_nome and p_nome.AsString()) else ""
        if not nome:
            p_alt = elem.LookupParameter(u"Nome do painel")
            nome = p_alt.AsString().strip() if (p_alt and p_alt.AsString()) else ""
        if not nome:
            p_marca = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            nome = p_marca.AsString().strip() if (p_marca and p_marca.AsString()) else ""

        elem_type = doc.GetElement(elem.GetTypeId())
        tipo_nome = ""
        fam_nome = ""
        if elem_type:
            try:
                tipo_nome = elem_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or ""
            except Exception:
                tipo_nome = ""
            fam_nome = getattr(elem_type, "FamilyName", "") or ""

        # Filtrar para apenas itens que comecem com 'Quadro' (no nome do painel ou no nome da família)
        comeca_com_quadro = False
        if nome and nome.strip().lower().startswith("quadro"):
            comeca_com_quadro = True
        elif fam_nome and fam_nome.strip().lower().startswith("quadro"):
            comeca_com_quadro = True

        if not comeca_com_quadro:
            continue

        if nome:
            if fam_nome:
                display_name = u"{} ({} - {})".format(nome, fam_nome, tipo_nome) if tipo_nome else u"{} ({})".format(nome, fam_nome)
            else:
                display_name = nome
        else:
            detalhe = u"{} {}".format(fam_nome, tipo_nome).strip()
            display_name = u"Quadro [Id: {}] {}".format(elem.Id.ToString(), detalhe).strip()

        quadros.append((display_name, elem))

    # Ordenar alfabeticamente
    quadros.sort(key=lambda x: x[0].lower())
    return quadros


class BombaDescricaoWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, element):
        super(BombaDescricaoWindow, self).__init__(xaml_file_name)
        self.element = element
        self.elem_type = doc.GetElement(element.GetTypeId())

        # 1. Ler e formatar os parâmetros individuais da bomba
        fase_raw = get_raw_value(self.element, self.elem_type, ["Fase", "Fase (padrão)"]) or ""
        fase_raw = fase_raw.strip()
        if fase_raw == "3":
            self.fase_str = u"Trifásico"
        elif fase_raw == "1":
            self.fase_str = u"Monofásica"
        else:
            self.fase_str = fase_raw or u"Indefinida"

        self.tensao_str = format_number(get_raw_value(self.element, self.elem_type, ["Tensão", "Tensão (padrão)"]))
        self.potencia_str = format_number(get_raw_value(self.element, self.elem_type, ["Potência_HP", "Potência_HP (padrão)"]), decimals=1)
        self.vazao_str = format_number(get_raw_value(self.element, self.elem_type, ["Vazão", "Vazão (padrão)"]))
        self.mca_str = format_number(get_raw_value(self.element, self.elem_type, ["MCA", "MCA (padrão)"]))

        # Preencher os campos da UI (parte superior)
        self.txtFase.Text = self.fase_str
        self.txtTensao.Text = self.tensao_str
        self.txtPotencia.Text = self.potencia_str
        self.txtVazao.Text = self.vazao_str
        self.txtMCA.Text = self.mca_str

        # 2. Gerar a descrição automática inicial da Bomba
        initial_desc = u"Motobomba Centrífuga Submersa, {}, {} V, {} CV, {} m³/h @ {} mca".format(
            self.fase_str, self.tensao_str, self.potencia_str, self.vazao_str, self.mca_str
        )
        self.txtFinalDesc.Text = initial_desc

        # 3. Determinar fase para o QCB (Monofásico ou Trifásico)
        if "3" in fase_raw or u"tri" in self.fase_str.lower():
            self.fase_qcb = u"Trifásico"
        elif "1" in fase_raw or u"mono" in self.fase_str.lower():
            self.fase_qcb = u"Monofásico"
        elif "2" in fase_raw or u"bi" in self.fase_str.lower():
            self.fase_qcb = u"Bifásico"
        else:
            self.fase_qcb = self.fase_str

        initial_qcb_desc = u"Quadro de Comando de Bomba {} CV {} V {}".format(
            self.potencia_str, self.tensao_str, self.fase_qcb
        )
        self.txtQCBDesc.Text = initial_qcb_desc

        # 4. Carregar lista de quadros do projeto no ComboBox
        self.quadros = []
        quadros_list = coletar_quadros_projeto(doc)
        if quadros_list:
            selected_index = 0
            qcb_found = False
            for idx, (disp_name, q_elem) in enumerate(quadros_list):
                self.quadros.append(q_elem)
                self.cmbQuadros.Items.Add(disp_name)
                # Tenta pré-selecionar se encontrar QCB ou Bomba no nome
                if not qcb_found and ("qcb" in disp_name.lower() or "bomba" in disp_name.lower()):
                    selected_index = idx
                    qcb_found = True
            self.cmbQuadros.SelectedIndex = selected_index
        else:
            self.chkNomearQCB.IsEnabled = False
            self.chkNomearQCB.Content = u"Nomear QCB também (Nenhum quadro compatível no projeto)"

        # Conectar eventos do CheckBox para garantir compatibilidade
        self.chkNomearQCB.Checked += self.OnChkQcbChanged
        self.chkNomearQCB.Unchecked += self.OnChkQcbChanged

    def OnChkQcbChanged(self, sender=None, e=None):
        if Visibility is not None:
            if self.chkNomearQCB.IsChecked:
                self.pnlQCB.Visibility = Visibility.Visible
            else:
                self.pnlQCB.Visibility = Visibility.Collapsed

    def SalvarClick(self, sender, e):
        final_text = self.txtFinalDesc.Text

        if not final_text.strip():
            forms.alert(u"O texto da descrição da bomba não pode estar vazio.", title=u"Atenção")
            return

        nomear_qcb = bool(self.chkNomearQCB.IsChecked)
        quadro_selecionado = None
        qcb_text = ""

        if nomear_qcb:
            if not self.quadros or self.cmbQuadros.SelectedIndex < 0:
                forms.alert(u"Por favor, selecione um Quadro de Comando no dropdown ou desmarque a opção.", title=u"Atenção")
                return

            qcb_text = self.txtQCBDesc.Text.strip()
            if not qcb_text:
                forms.alert(u"A descrição do QCB não pode estar vazia.", title=u"Atenção")
                return

            quadro_selecionado = self.quadros[self.cmbQuadros.SelectedIndex]

            # Garante que o parâmetro 'Descrição do Material' existe para o Quadro
            if not garantir_parametro_descricao(quadro_selecionado):
                return

        with revit.Transaction(u"Atualizar Descrição da Bomba e QCB"):
            # 1. Gravar na Bomba
            target_param = self.elem_type.LookupParameter("Descrição do Material") if self.elem_type else None
            if not target_param:
                target_param = self.element.LookupParameter("Descrição do Material")

            if target_param and not target_param.IsReadOnly:
                target_param.Set(final_text)
            else:
                forms.alert(u"O parâmetro de destino na bomba não foi encontrado ou é de leitura.", title=u"Erro")
                return

            # 2. Gravar no Quadro (QCB)
            if nomear_qcb and quadro_selecionado:
                quadro_type = doc.GetElement(quadro_selecionado.GetTypeId())
                target_param_qcb = quadro_selecionado.LookupParameter("Descrição do Material")
                if not target_param_qcb and quadro_type:
                    target_param_qcb = quadro_type.LookupParameter("Descrição do Material")

                if target_param_qcb and not target_param_qcb.IsReadOnly:
                    target_param_qcb.Set(qcb_text)
                else:
                    forms.alert(u"O parâmetro 'Descrição do Material' do Quadro não foi encontrado ou é de leitura.", title=u"Erro")
                    return

        # Mensagem final de sucesso
        if nomear_qcb:
            forms.alert(
                u"Parâmetros gravados com sucesso!\n\n"
                u"• Bomba:\n{}\n\n"
                u"• Quadro de Comando (QCB):\n{}".format(final_text, qcb_text),
                title=u"Concluído"
            )
        else:
            forms.alert(u"Descrição da bomba gravada com sucesso:\n\n{}".format(final_text), title=u"Concluído")

        self.Close()


if __name__ == "__main__":
    selection = revit.get_selection().elements

    if not selection:
        forms.alert(u"Selecione uma bomba no projeto antes de clicar no botão!", title=u"Atenção", exitscript=True)
    
    if len(selection) > 1:
        forms.alert(u"Por favor, selecione apenas uma bomba de cada vez para editar os dados na janela.", title=u"Atenção", exitscript=True)

    target_element = selection[0]
    elem_type = doc.GetElement(target_element.GetTypeId())

    # --- 1. VALIDAÇÃO DE SEGURANÇA: PARÂMETROS DA BOMBA ---
    parametros_obrigatorios = [
        ["Fase", "Fase (padrão)"],
        ["Tensão", "Tensão (padrão)"],
        ["Potência_HP", "Potência_HP (padrão)"],
        ["Vazão", "Vazão (padrão)"],
        ["MCA", "MCA (padrão)"]
    ]

    faltando = []
    for grupo in parametros_obrigatorios:
        encontrado = False
        for nome in grupo:
            p_inst = target_element.LookupParameter(nome)
            p_tipo = elem_type.LookupParameter(nome) if elem_type else None
            if (p_inst and p_inst.HasValue) or (p_tipo and p_tipo.HasValue):
                encontrado = True
                break
        if not encontrado:
            faltando.append(grupo[0])

    if faltando:
        aviso_faltando = "\n".join(["- {}".format(p) for p in faltando])
        forms.alert(
            u"O elemento selecionado não é uma bomba compatível ou faltam os seguintes parâmetros:\n\n{}\n\nCertifique-se de selecionar a família correta!".format(aviso_faltando),
            title=u"Erro: Parâmetros em Falta",
            exitscript=True
        )

    # --- 2. PERGUNTAR E CRIAR O PARÂMETRO NA BOMBA SE NÃO EXISTIR ---
    if not garantir_parametro_descricao(target_element):
        import sys
        sys.exit()

    xaml_path = os.path.join(os.path.dirname(__file__), "UI.xaml")
    BombaDescricaoWindow(xaml_path, target_element).ShowDialog()
