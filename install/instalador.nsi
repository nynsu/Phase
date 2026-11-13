; ========================================================
; INSTALADOR MODERNO COM NSIS (MUI2)
; Extensao: Phase (para Autodesk Revit via pyRevit)
; ========================================================

Unicode True

; 1. Informacoes Gerais e Versao
!define VERSION "1.2.0"
Name "Phase ${VERSION}"
OutFile "..\dist\Instalador_Phase_v${VERSION}.exe"

; CAMINHO FIXO (Obrigatorio do pyRevit):
InstallDir "$APPDATA\pyRevit\Extensions\Phase.extension"
RequestExecutionLevel user

; 2. Interface Moderna (MUI2)
!include "MUI2.nsh"

!define MUI_ABORTWARNING

; --------------------------------------------------------
; Identidade Visual (Ícone e Banners Oficiais do Phase)
; --------------------------------------------------------
!define MUI_ICON "icone.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "banner_lateral.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "logo_topo.bmp"

; --------------------------------------------------------
; 3. Fluxo de Telas
; --------------------------------------------------------
; Tela 1: Boas-vindas personalizada para contexto BIM / Revit
!define MUI_WELCOMEPAGE_TITLE "Bem-vindo ao Instalador do Phase ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "O assistente configurará o Phase no Autodesk Revit via pyRevit.$\r$\n$\r$\nImportante: Certifique-se de que o Autodesk Revit está completamente fechado antes de continuar para que a extensão seja registrada com sucesso.$\r$\n$\r$\nClique em Avançar para prosseguir."
!insertmacro MUI_PAGE_WELCOME

; Tela 2: Termos de Licenca (Botão direto de ação limpo)
!define MUI_LICENSEPAGE_BUTTON "Instalar"
!insertmacro MUI_PAGE_LICENSE "licenca.rtf"

; (Tela de Selecao de Pasta REMOVIDA para o caminho ser 100% fixo no pyRevit)

; Tela 3: Instalando arquivos com barra de progresso
!insertmacro MUI_PAGE_INSTFILES

; Tela 4: Conclusao
!insertmacro MUI_PAGE_FINISH

; 4. Idioma (DEVE VIR DEPOIS DAS PAGINAS)
!insertmacro MUI_LANGUAGE "PortugueseBR"

; 5. Acao de Instalacao
Section "Instalar Phase" SecInstalar
    ; Cria e descarrega os arquivos diretamente no caminho fixo do pyRevit
    SetOutPath "$INSTDIR"
    File "..\extension.json"
    File "..\LICENSE"
    File "..\README.md"
    
    ; Copia a estrutura de abas ignorando pastas de desenvolvimento privado/beta
    SetOutPath "$INSTDIR\Phase.tab"
    File /r /x "Beta.panel" /x "*.pyc" /x "__pycache__" "..\Phase.tab\*.*"
SectionEnd
