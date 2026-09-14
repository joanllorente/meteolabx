/**
 * Textos de la tarjeta de instalación, en los siete idiomas de la web.
 *
 * Los nombres de los menús son los que enseña cada navegador en ese idioma
 * —«Añadir a pantalla de inicio», «Zum Home-Bildschirm»…—: si no coinciden
 * con lo que la persona ve, las instrucciones no sirven.
 *
 * `{share}` y `{add}` se dibujan como los iconos de iOS: mucha gente no sabe
 * cuál es el botón Compartir hasta que lo ve.
 */
const TEXTS = {
  es: {
    title: 'Instala MeteoLabX',
    subtitle: 'Ábrela como una app: a un toque y a pantalla completa.',
    install: 'Instalar',
    how: 'Cómo instalar',
    hide: 'Ocultar instrucciones',
    dismiss: 'Ahora no',
    installed: 'Instalada. Ya puedes abrirla desde tu pantalla de inicio o tu escritorio.',
    steps: {
      'ios-safari': [
        'Toca el botón Compartir {share}, abajo en iPhone o arriba en iPad.',
        'Desliza la lista y elige {add} «Añadir a pantalla de inicio».',
        'Pulsa «Añadir».'
      ],
      'ios-share': [
        'Toca el botón Compartir {share} del navegador (en Chrome, a la derecha de la barra de direcciones).',
        'Elige {add} «Añadir a pantalla de inicio» y pulsa «Añadir».',
        'Necesita iOS 16.4 o posterior. Si no aparece, abre esta página en Safari.'
      ],
      'android-menu': [
        'Abre el menú ⋮ del navegador.',
        'Toca «Instalar aplicación» o «Añadir a pantalla de inicio».',
        'Confirma con «Instalar».'
      ],
      'android-samsung': [
        'Abre el menú ≡ de Samsung Internet.',
        'Toca «Añadir página a» y elige «Pantalla de inicio».'
      ],
      'android-firefox': [
        'Abre el menú ⋮ de Firefox.',
        'Toca «Añadir a la pantalla de inicio» y confirma.'
      ],
      'mac-safari': [
        'En la barra de menús, abre «Archivo».',
        'Elige «Añadir al Dock» y pulsa «Añadir». Necesita macOS Sonoma o posterior.'
      ],
      'desktop-chromium': [
        'Pulsa el icono de instalar a la derecha de la barra de direcciones.',
        'Si no está, abre el menú ⋮ (⋯ en Edge) y busca «Instalar MeteoLabX» o «Instalar este sitio como aplicación».'
      ],
      'open-browser': [
        'Estás dentro de otra app, que no deja instalar webs.',
        'Abre esta página en tu navegador (menú ⋯ → «Abrir en el navegador») e instálala desde allí.'
      ],
      unsupported: [
        'Este navegador no instala aplicaciones web.',
        'Abre MeteoLabX en Chrome o Edge, o en Safari si usas Mac, e instálala desde allí.'
      ]
    }
  },
  ca: {
    title: 'Instal·la MeteoLabX',
    subtitle: 'Obre-la com una app: a un toc i a pantalla completa.',
    install: 'Instal·la',
    how: 'Com instal·lar-la',
    hide: 'Amaga les instruccions',
    dismiss: 'Ara no',
    installed: 'Instal·lada. Ja la pots obrir des de la pantalla d’inici o l’escriptori.',
    steps: {
      'ios-safari': [
        'Toca el botó Comparteix {share}, a baix a l’iPhone o a dalt a l’iPad.',
        'Llisca la llista i tria {add} «Afegeix a la pantalla d’inici».',
        'Prem «Afegeix».'
      ],
      'ios-share': [
        'Toca el botó Comparteix {share} del navegador (a Chrome, a la dreta de la barra d’adreces).',
        'Tria {add} «Afegeix a la pantalla d’inici» i prem «Afegeix».',
        'Cal iOS 16.4 o posterior. Si no apareix, obre aquesta pàgina a Safari.'
      ],
      'android-menu': [
        'Obre el menú ⋮ del navegador.',
        'Toca «Instal·la l’aplicació» o «Afegeix a la pantalla d’inici».',
        'Confirma amb «Instal·la».'
      ],
      'android-samsung': [
        'Obre el menú ≡ de Samsung Internet.',
        'Toca «Afegeix la pàgina a» i tria «Pantalla d’inici».'
      ],
      'android-firefox': [
        'Obre el menú ⋮ de Firefox.',
        'Toca «Afegeix a la pantalla d’inici» i confirma.'
      ],
      'mac-safari': [
        'A la barra de menús, obre «Arxiu».',
        'Tria «Afegeix al Dock» i prem «Afegeix». Cal macOS Sonoma o posterior.'
      ],
      'desktop-chromium': [
        'Prem la icona d’instal·lar a la dreta de la barra d’adreces.',
        'Si no hi és, obre el menú ⋮ (⋯ a Edge) i cerca «Instal·la MeteoLabX» o «Instal·la aquest lloc com a aplicació».'
      ],
      'open-browser': [
        'Ets dins d’una altra app, que no deixa instal·lar webs.',
        'Obre aquesta pàgina al navegador (menú ⋯ → «Obre al navegador») i instal·la-la des d’allà.'
      ],
      unsupported: [
        'Aquest navegador no instal·la aplicacions web.',
        'Obre MeteoLabX a Chrome o Edge, o a Safari si fas servir Mac, i instal·la-la des d’allà.'
      ]
    }
  },
  en: {
    title: 'Install MeteoLabX',
    subtitle: 'Open it like an app: one tap away and full screen.',
    install: 'Install',
    how: 'How to install',
    hide: 'Hide instructions',
    dismiss: 'Not now',
    installed: 'Installed. You can now open it from your home screen or desktop.',
    steps: {
      'ios-safari': [
        'Tap the Share button {share}, at the bottom on iPhone or the top on iPad.',
        'Scroll the list and choose {add} “Add to Home Screen”.',
        'Tap “Add”.'
      ],
      'ios-share': [
        'Tap your browser’s Share button {share} (in Chrome, at the right of the address bar).',
        'Choose {add} “Add to Home Screen” and tap “Add”.',
        'Requires iOS 16.4 or later. If it is not there, open this page in Safari.'
      ],
      'android-menu': [
        'Open the browser’s ⋮ menu.',
        'Tap “Install app” or “Add to Home screen”.',
        'Confirm with “Install”.'
      ],
      'android-samsung': [
        'Open the ≡ menu in Samsung Internet.',
        'Tap “Add page to” and choose “Home screen”.'
      ],
      'android-firefox': [
        'Open Firefox’s ⋮ menu.',
        'Tap “Add to Home screen” and confirm.'
      ],
      'mac-safari': [
        'In the menu bar, open “File”.',
        'Choose “Add to Dock” and click “Add”. Requires macOS Sonoma or later.'
      ],
      'desktop-chromium': [
        'Click the install icon at the right of the address bar.',
        'If it is not there, open the ⋮ menu (⋯ in Edge) and look for “Install MeteoLabX” or “Install this site as an app”.'
      ],
      'open-browser': [
        'You are inside another app, which cannot install websites.',
        'Open this page in your browser (⋯ menu → “Open in browser”) and install it from there.'
      ],
      unsupported: [
        'This browser does not install web apps.',
        'Open MeteoLabX in Chrome or Edge, or Safari on a Mac, and install it from there.'
      ]
    }
  },
  de: {
    title: 'MeteoLabX installieren',
    subtitle: 'Wie eine App öffnen: mit einem Tipp und im Vollbild.',
    install: 'Installieren',
    how: 'So installierst du sie',
    hide: 'Anleitung ausblenden',
    dismiss: 'Nicht jetzt',
    installed: 'Installiert. Du kannst sie jetzt vom Home-Bildschirm oder Desktop öffnen.',
    steps: {
      'ios-safari': [
        'Tippe auf „Teilen“ {share}, auf dem iPhone unten, auf dem iPad oben.',
        'Scrolle in der Liste und wähle {add} „Zum Home-Bildschirm“.',
        'Tippe auf „Hinzufügen“.'
      ],
      'ios-share': [
        'Tippe auf die Teilen-Taste {share} des Browsers (in Chrome rechts neben der Adressleiste).',
        'Wähle {add} „Zum Home-Bildschirm“ und tippe auf „Hinzufügen“.',
        'Benötigt iOS 16.4 oder neuer. Falls die Option fehlt, öffne diese Seite in Safari.'
      ],
      'android-menu': [
        'Öffne das ⋮-Menü des Browsers.',
        'Tippe auf „App installieren“ oder „Zum Startbildschirm hinzufügen“.',
        'Bestätige mit „Installieren“.'
      ],
      'android-samsung': [
        'Öffne das ≡-Menü von Samsung Internet.',
        'Tippe auf „Seite hinzufügen zu“ und wähle „Startbildschirm“.'
      ],
      'android-firefox': [
        'Öffne das ⋮-Menü von Firefox.',
        'Tippe auf „Zum Startbildschirm hinzufügen“ und bestätige.'
      ],
      'mac-safari': [
        'Öffne in der Menüleiste „Ablage“.',
        'Wähle „Zum Dock hinzufügen“ und klicke auf „Hinzufügen“. Benötigt macOS Sonoma oder neuer.'
      ],
      'desktop-chromium': [
        'Klicke auf das Installationssymbol rechts in der Adressleiste.',
        'Falls es fehlt, öffne das ⋮-Menü (⋯ in Edge) und suche „MeteoLabX installieren“ oder „Diese Website als App installieren“.'
      ],
      'open-browser': [
        'Du bist in einer anderen App, die keine Websites installieren kann.',
        'Öffne diese Seite im Browser (⋯-Menü → „Im Browser öffnen“) und installiere sie dort.'
      ],
      unsupported: [
        'Dieser Browser kann keine Web-Apps installieren.',
        'Öffne MeteoLabX in Chrome oder Edge, auf dem Mac auch in Safari, und installiere sie dort.'
      ]
    }
  },
  fr: {
    title: 'Installer MeteoLabX',
    subtitle: 'Ouvrez-la comme une app : en un geste et en plein écran.',
    install: 'Installer',
    how: 'Comment l’installer',
    hide: 'Masquer les instructions',
    dismiss: 'Plus tard',
    installed: 'Installée. Vous pouvez l’ouvrir depuis l’écran d’accueil ou le bureau.',
    steps: {
      'ios-safari': [
        'Touchez le bouton Partager {share}, en bas sur iPhone ou en haut sur iPad.',
        'Faites défiler et choisissez {add} « Sur l’écran d’accueil ».',
        'Touchez « Ajouter ».'
      ],
      'ios-share': [
        'Touchez le bouton Partager {share} du navigateur (dans Chrome, à droite de la barre d’adresse).',
        'Choisissez {add} « Sur l’écran d’accueil » puis « Ajouter ».',
        'Nécessite iOS 16.4 ou ultérieur. Si l’option manque, ouvrez cette page dans Safari.'
      ],
      'android-menu': [
        'Ouvrez le menu ⋮ du navigateur.',
        'Touchez « Installer l’application » ou « Ajouter à l’écran d’accueil ».',
        'Confirmez avec « Installer ».'
      ],
      'android-samsung': [
        'Ouvrez le menu ≡ de Samsung Internet.',
        'Touchez « Ajouter la page à » puis « Écran d’accueil ».'
      ],
      'android-firefox': [
        'Ouvrez le menu ⋮ de Firefox.',
        'Touchez « Ajouter à l’écran d’accueil » et confirmez.'
      ],
      'mac-safari': [
        'Dans la barre des menus, ouvrez « Fichier ».',
        'Choisissez « Ajouter au Dock » puis « Ajouter ». Nécessite macOS Sonoma ou ultérieur.'
      ],
      'desktop-chromium': [
        'Cliquez sur l’icône d’installation à droite de la barre d’adresse.',
        'Si elle n’y est pas, ouvrez le menu ⋮ (⋯ dans Edge) et cherchez « Installer MeteoLabX » ou « Installer ce site en tant qu’application ».'
      ],
      'open-browser': [
        'Vous êtes dans une autre app, qui ne peut pas installer de sites.',
        'Ouvrez cette page dans votre navigateur (menu ⋯ → « Ouvrir dans le navigateur ») et installez-la depuis là.'
      ],
      unsupported: [
        'Ce navigateur n’installe pas les applications web.',
        'Ouvrez MeteoLabX dans Chrome ou Edge, ou dans Safari sur Mac, et installez-la depuis là.'
      ]
    }
  },
  it: {
    title: 'Installa MeteoLabX',
    subtitle: 'Aprila come un’app: a un tocco e a schermo intero.',
    install: 'Installa',
    how: 'Come installarla',
    hide: 'Nascondi istruzioni',
    dismiss: 'Non ora',
    installed: 'Installata. Ora puoi aprirla dalla schermata Home o dal desktop.',
    steps: {
      'ios-safari': [
        'Tocca il pulsante Condividi {share}, in basso su iPhone o in alto su iPad.',
        'Scorri l’elenco e scegli {add} «Aggiungi alla schermata Home».',
        'Tocca «Aggiungi».'
      ],
      'ios-share': [
        'Tocca il pulsante Condividi {share} del browser (in Chrome, a destra della barra degli indirizzi).',
        'Scegli {add} «Aggiungi alla schermata Home» e tocca «Aggiungi».',
        'Serve iOS 16.4 o successivo. Se non compare, apri questa pagina in Safari.'
      ],
      'android-menu': [
        'Apri il menu ⋮ del browser.',
        'Tocca «Installa app» o «Aggiungi a schermata Home».',
        'Conferma con «Installa».'
      ],
      'android-samsung': [
        'Apri il menu ≡ di Samsung Internet.',
        'Tocca «Aggiungi pagina a» e scegli «Schermata Home».'
      ],
      'android-firefox': [
        'Apri il menu ⋮ di Firefox.',
        'Tocca «Aggiungi alla schermata principale» e conferma.'
      ],
      'mac-safari': [
        'Nella barra dei menu, apri «File».',
        'Scegli «Aggiungi al Dock» e fai clic su «Aggiungi». Serve macOS Sonoma o successivo.'
      ],
      'desktop-chromium': [
        'Fai clic sull’icona di installazione a destra della barra degli indirizzi.',
        'Se non c’è, apri il menu ⋮ (⋯ in Edge) e cerca «Installa MeteoLabX» o «Installa questo sito come app».'
      ],
      'open-browser': [
        'Sei dentro un’altra app, che non può installare siti web.',
        'Apri questa pagina nel browser (menu ⋯ → «Apri nel browser») e installala da lì.'
      ],
      unsupported: [
        'Questo browser non installa app web.',
        'Apri MeteoLabX in Chrome o Edge, o in Safari su Mac, e installala da lì.'
      ]
    }
  },
  pt: {
    title: 'Instalar o MeteoLabX',
    subtitle: 'Abra-o como uma app: a um toque e em ecrã inteiro.',
    install: 'Instalar',
    how: 'Como instalar',
    hide: 'Ocultar instruções',
    dismiss: 'Agora não',
    installed: 'Instalado. Já o pode abrir a partir do ecrã principal ou do ambiente de trabalho.',
    steps: {
      'ios-safari': [
        'Toque no botão Partilhar {share}, em baixo no iPhone ou em cima no iPad.',
        'Deslize a lista e escolha {add} «Adicionar ao ecrã principal».',
        'Toque em «Adicionar».'
      ],
      'ios-share': [
        'Toque no botão Partilhar {share} do navegador (no Chrome, à direita da barra de endereço).',
        'Escolha {add} «Adicionar ao ecrã principal» e toque em «Adicionar».',
        'Requer iOS 16.4 ou posterior. Se não aparecer, abra esta página no Safari.'
      ],
      'android-menu': [
        'Abra o menu ⋮ do navegador.',
        'Toque em «Instalar app» ou «Adicionar ao ecrã principal».',
        'Confirme com «Instalar».'
      ],
      'android-samsung': [
        'Abra o menu ≡ do Samsung Internet.',
        'Toque em «Adicionar página a» e escolha «Ecrã principal».'
      ],
      'android-firefox': [
        'Abra o menu ⋮ do Firefox.',
        'Toque em «Adicionar ao ecrã principal» e confirme.'
      ],
      'mac-safari': [
        'Na barra de menus, abra «Ficheiro».',
        'Escolha «Adicionar ao Dock» e clique em «Adicionar». Requer macOS Sonoma ou posterior.'
      ],
      'desktop-chromium': [
        'Clique no ícone de instalar à direita da barra de endereço.',
        'Se não estiver lá, abra o menu ⋮ (⋯ no Edge) e procure «Instalar MeteoLabX» ou «Instalar este site como aplicação».'
      ],
      'open-browser': [
        'Está dentro de outra app, que não deixa instalar sites.',
        'Abra esta página no navegador (menu ⋯ → «Abrir no navegador») e instale-a a partir daí.'
      ],
      unsupported: [
        'Este navegador não instala aplicações web.',
        'Abra o MeteoLabX no Chrome ou no Edge, ou no Safari num Mac, e instale-o a partir daí.'
      ]
    }
  }
};

export function installText(language) {
  return TEXTS[language] || TEXTS.en;
}

export const INSTALL_LANGUAGES = Object.keys(TEXTS);
