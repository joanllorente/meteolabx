/**
 * Les guides des cartes, en français.
 *
 * Une traduction de `forecastProductGuides.js`, conservée dans un module à
 * part pour n'être téléchargée que par les lecteurs francophones : les six
 * langues réunies ajouteraient un demi-mégaoctet de texte dont chaque
 * visiteur n'utilise qu'un sixième.
 *
 * Les produits absents d'ici retombent sur le guide castillan avec un avis.
 * Les clés doivent correspondre exactement aux identifiants des produits.
 */
const ECMWF_OPEN_DATA = {
  label: 'ECMWF · Données ouvertes en temps réel',
  url: 'https://www.ecmwf.int/en/forecasts/datasets/open-data'
};

const MF_API = {
  label: 'Météo-France · API ciblée modèles (WCS/WMS)',
  url: 'https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/854032416/API+Cibl+e+Mod+les'
};

const MF_AROME = {
  label: 'Météo-France · fiche officielle de l’API AROME',
  url: 'https://portail-api.meteofrance.fr/web/fr/api/AROME'
};

const NOAA_CAPE = {
  label: 'NOAA/NWS · paramètres convectifs et interprétation de la CAPE',
  url: 'https://www.weather.gov/lmk/indices'
};

const NOAA_LI = {
  label: 'NOAA/NWS · définition et interprétation de la CAPE et du Lifted Index',
  url: 'https://www.weather.gov/mlb/adas_glossary'
};

const SHARPPY = {
  label: 'SHARPpy · implémentation publique de params.py',
  url: 'https://github.com/sharppy/SHARPpy/blob/main/sharppy/sharptab/params.py'
};

const THOMPSON_2007 = {
  label: 'Thompson, Mead et Edwards (2007) · couche effective et EBWD',
  url: 'https://doi.org/10.1175/WAF969.1'
};

const RKW_1988 = {
  label: 'Rotunno, Klemp et Weisman (1988) · équilibre cisaillement–cold pool',
  url: 'https://doi.org/10.1175/1520-0469(1988)045%3C0463:ATFSLL%3E2.0.CO;2'
};

const BOLTON_1980 = {
  label: 'Bolton (1980) · calcul de la température potentielle équivalente',
  url: 'https://doi.org/10.1175/1520-0493(1980)108%3C1046:TCOEPT%3E2.0.CO;2'
};

const METPY = {
  label: 'MetPy · equivalent_potential_temperature',
  url: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.equivalent_potential_temperature.html'
};

const NAYLOR_2012 = {
  label: 'Naylor et al. (2012) · sensibilité de l’hélicité de l’ascendance simulée',
  url: 'https://journals.ametsoc.org/view/journals/mwre/140/7/mwr-d-11-00209.1.xml'
};

const LINE_ORIENTATION = {
  label: 'Bluestein et Weisman (2000) · orientation du cisaillement par rapport à la ligne de déclenchement',
  url: 'https://journals.ametsoc.org/view/journals/mwre/128/9/1520-0493_2000_128_3128_tionss_2.0.co_2.xml'
};

const CELL_MOTION = {
  label: 'NOAA/NSSL · advection et propagation du mouvement convectif',
  url: 'https://www.nssl.noaa.gov/users/brooks/public_html/sls19/abstracts/corfidi3.html'
};

export const forecastProductGuides = {
  'precip-type': {
    what: 'Forme sous laquelle atteindrait le sol la précipitation prévue par AROME dans chaque maille durant l’heure sélectionnée : liquide, gelée ou un mélange. Ce n’est pas la quantité mais la nature de ce qui tombe. Cela dépend surtout des températures que traverse la précipitation en tombant : presque tout naît sous forme de glace ou de neige dans les nuages, et ce qui arrive en bas dépend de ce qu’elle rencontre en chemin, des couches au-dessus de 0 °C où elle fond, puis des couches froides où elle regèle. La carte distingue onze classes, en comptant l’absence de précipitation.',
    interpretation: [
      'Pluie : gouttes d’eau liquide de plus d’un demi-millimètre. C’est le cas habituel quand la couche au-dessus de 0 °C est assez épaisse pour fondre entièrement la neige qui tombe des nuages.',
      'Bruine : gouttes très fines, de moins d’un demi-millimètre et très rapprochées, qui semblent flotter. Elle provient de nuages bas et peu épais, comme les stratus ou les brouillards denses, et laisse de petites quantités bien qu’elle puisse mouiller beaucoup pendant des heures.',
      'Précipitations verglaçantes et bruine verglaçante : elles arrivent liquides mais en dessous de 0 °C, en surfusion, et gèlent instantanément au contact du sol, des voitures, des câbles ou des arbres. Elles forment une couche de glace transparente, le verglas, très dangereuse sur les routes. Ce phénomène apparaît quand la neige fond dans une couche plus douce en altitude puis retombe à travers une couche froide collée au sol, trop fine pour la regeler.',
      'Granules de glace : petites billes de glace transparente, dures, qui rebondissent en tombant. Ce sont des gouttes de pluie qui ont regelé dans l’air. La situation ressemble à celle des précipitations verglaçantes, mais avec une couche froide près du sol assez épaisse pour geler les gouttes avant qu’elles n’arrivent.',
      'Neige sèche : flocons qui arrivent sans avoir fondu, légers et détachés, typiques quand l’air est sous zéro sur toute la colonne. Elle tient facilement et le vent la soulève et l’accumule en congères.',
      'Neige humide : flocons qui ont commencé à fondre, gros et lourds, avec de l’eau liquide à l’intérieur. Elle tombe avec des températures proches de 0 °C en surface. Elle colle aux arbres, aux câbles et aux toitures, et c’est celle qui, par son poids, casse le plus de branches et de lignes électriques. La carte y inclut la neige particulièrement collante que le modèle diagnostique à part.',
      'Pluie et neige : gouttes et flocons à demi fondus arrivent en même temps, ce qu’on appelle la pluie et neige mêlées. Elle marque la transition entre pluie et neige et apparaît généralement juste autour de la limite pluie-neige.',
      'Grésil : petites billes blanches et opaques de moins de 5 mm, tendres, qui s’écrasent entre les doigts. Elles se forment quand un flocon capte des gouttelettes en surfusion qui gèlent à sa surface jusqu’à lui faire perdre sa forme cristalline. C’est typique des averses d’air froid, souvent orageuses, en hiver et au printemps. À ne pas confondre avec les granules de glace, qui sont durs et transparents.',
      'Grêle : grêlons de 5 mm ou plus, durs et souvent formés en couches successives. Ils se développent à l’intérieur des orages, où les courants ascendants les maintiennent en suspension pendant que l’eau gèle à leur surface. La carte n’indique pas la taille que pourraient atteindre les grêlons.',
      'Le grésil et la grêle ne dépendent pas des températures à la chute, mais de la quantité de grésil que le modèle simule à l’intérieur de l’orage : une quantité modérée fait classer la maille en grésil, et une grande quantité, en grêle.',
      'À lire avec les cartes de limite pluie-neige et d’isotherme 0 °C, qui expliquent pourquoi il pleut à un endroit et neige à un autre, et avec la précipitation, qui indique la quantité. Une maille de 2,5 km n’est pas une observation ponctuelle : aux frontières entre deux types, surtout en relief accidenté, la réalité peut se situer à quelques kilomètres ou quelques centaines de mètres d’altitude de ce que place le modèle.'
    ],
    method: 'MeteoLabX affiche le diagnostic de type de précipitation publié par AROME pour chaque heure, sans le recalculer. Le modèle détermine le type à partir du profil vertical de température et des hydrométéores qu’il simule dans la colonne, et, pour le grésil et la grêle, de la quantité de grésil à l’intérieur du nuage. Certaines catégories du modèle sont des variantes d’une autre, comme la précipitation intermittente ou la neige collante, et sont regroupées avec leur type principal pour une légende plus claire.',
    equations: [],
    steps: [
      'Lire le type de précipitation de l’heure valide sur tout le domaine d’AROME.',
      'Regrouper les variantes : la pluie, la neige et le mélange intermittents passent à leur type principal, et la neige collante, à la neige humide.',
      'Colorer chaque type d’une couleur fixe, sans mélanger les couleurs entre catégories. Les mailles sans donnée ou avec un type que le modèle ne documente pas restent transparentes.'
    ],
    sources: [{ label: 'Météo-France · codes et diagnostic PTYPE d’AROME', url: 'https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/1674051588/Comprendre+les+diagnostics+de+type+de+pr+cipitation+dans+les+mod+les+AROME+de+M+t+o-France' }]
  },

  'stp': {
    what: 'Significant Tornado Parameter de couche effective avec CIN : indice sans dimension qui résume les ingrédients de l’environnement associés aux tornades significatives dans les supercellules déviées à droite.',
    interpretation: ['Une valeur élevée indique une coïncidence d’ingrédients, pas une probabilité ni la garantie qu’une tornade se forme. Cela dépend aussi de l’initiation et du mode convectif.', 'La base effective doit atteindre le sol. Si elle est surélevée, l’indice est annulé ; une base inconnue reste sans donnée.', 'Des bases nuageuses hautes et une forte inhibition réduisent l’indice. Une valeur faible n’exclut pas des tornades dans des configurations que ce composite ne représente pas.'],
    method: 'Version effective du STP du Storm Prediction Center (SPC). Il combine cinq ingrédients du même profil et de la même heure : l’énergie (MLCAPE) et l’inhibition (MLCIN) d’une particule représentant l’air moyen des 100 hPa les plus bas, la hauteur de sa base nuageuse (NCS) au-dessus du terrain, ainsi que l’hélicité (ESRH) et le cisaillement (EBWD) de la couche qui alimenterait l’orage.',
    equations: [{ label: 'STP effectif avec CIN', latex: String.raw`\mathrm{STP}=\frac{\mathrm{MLCAPE}}{1500}\frac{\mathrm{ESRH}}{150}\,f_{\mathrm{LCL}}\,f_{\mathrm{EBWD}}\,f_{\mathrm{CIN}}` }],
    steps: ['NCS : facteur (2000 − MLLCL)/1000, limité entre zéro et un ; hauteurs en mètres au-dessus du sol.', 'CIN : facteur (MLCIN + 200)/150, limité entre zéro et un ; CIN négative en J/kg.', 'EBWD : zéro sous 12,5 m/s, EBWD/20 jusqu’à 30 m/s et un maximum de 1,5.', 'Avec une base effective surélevée, STP vaut zéro. Les ingrédients absents laissent le résultat sans donnée. L’indice est limité à la plage non négative pour le mouvement de Bunkers à droite.'],
    sources: [{ label: 'SPC · Définition du STP effectif', url: 'https://origin-west-www-spc.woc.noaa.gov/exper/mesoanalysis/help/begin.html' }, { label: 'NOAA · Seuils du STP', url: 'https://vlab.noaa.gov/web/oclo/nsharp-hail-and-tornado-reference' }]
  },

  'esrh': {
    what: 'Hélicité relative à l’orage dans la couche d’alimentation effective (ESRH), en m²/s². La base peut être surélevée par rapport au sol.',
    interpretation: [
      'Des valeurs positives indiquent une hélicité favorable à la supercellule déviée à droite de Bunkers. Le signe est conservé ; les flèches représentent son mouvement estimé.',
      'La couche effective sélectionne l’air capable d’alimenter la convection. Elle n’équivaut pas à la SRH 0–1 km ni 0–3 km, et n’est pas à elle seule une prévision de tornades.',
      'Sans couche d’épaisseur positive et de limites définies, ou sans couverture complète du vent, aucune valeur n’est publiée.'
    ],
    method: 'On teste comme origine de particule chaque niveau, de la surface vers le haut, jusqu’à trouver la couche effective : la première séquence continue de particules avec CAPE ≥ 100 J/kg et CIN ≥ −250 J/kg. La base et le sommet sont le premier et le dernier niveau qui satisfont ce critère, la séquence se refermant au niveau suivant qui échoue. Un sommet non observé reste sans donnée. L’intégrale utilise tous les segments de l’hodographe, découpés aux deux limites, et le mouvement de Bunkers déjà calculé.',
    equations: [{ label: 'Hélicité effective', latex: String.raw`\mathrm{ESRH}=\int_{z_b}^{z_t}\left[(v-C_v)\frac{\partial u}{\partial z}-(u-C_u)\frac{\partial v}{\partial z}\right]dz` }],
    steps: ['Tester comme origine de particule chaque niveau, de la surface vers le haut, jusqu’à trouver la couche effective.', 'Conserver la base effective utilisée par EBWD et déterminer le sommet lors du même parcours.', 'Intégrer le vent relatif au mouvement de Bunkers entre la base et le sommet.'],
    sources: [{ label: 'SPC · Effective storm-relative helicity', url: 'https://www.spc.noaa.gov/exper/mesoanalysis/help/help_esrh.html' }]
  },

  'scp': {
    what: 'Supercell Composite Parameter de couche effective : indice sans dimension combinant instabilité, hélicité effective et cisaillement effectif.',
    interpretation: ['Des valeurs positives croissantes indiquent une plus grande coïncidence d’ingrédients favorables aux supercellules déviées à droite ; elles ne représentent pas une probabilité.', 'Cela ne garantit ni le déclenchement ni un mode d’orage isolé. À lire avec le forçage, l’inhibition et l’évolution prévue.', 'L’échelle montre la plage positive. Le diagnostic conserve le signe de l’ESRH et les données absentes ; un ingrédient manquant n’est pas remplacé par zéro.'],
    method: 'Formulation SPC de couche effective, calculée directement à partir de la MUCAPE, de l’ESRH et de l’EBWD partagées. Le facteur de cisaillement est nul pour EBWD < 10 m/s, vaut EBWD/20 entre 10 et 20 m/s, et un pour EBWD > 20 m/s.',
    equations: [{ label: 'SCP', latex: String.raw`\mathrm{SCP}=\frac{\mathrm{MUCAPE}}{1000\,\mathrm{J\,kg^{-1}}}\frac{\mathrm{ESRH}}{50\,\mathrm{m^2\,s^{-2}}}f(\mathrm{EBWD})` }],
    steps: ['Obtenir les trois ingrédients de la même échéance, du même profil et de la même grille.', 'Appliquer les seuils d’EBWD en m/s et multiplier les facteurs normalisés.'],
    sources: [{ label: 'MetPy · SCP et formulation SPC', url: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.supercell_composite.html' }]
  },

  'z500-mslp': {
    what: 'Altitude géopotentielle à 500 hPa en couleur et pression au niveau de la mer en isobares, sur l’Atlantique et l’Europe. C’est le duo classique de l’analyse synoptique : l’altitude de la surface de 500 hPa dessine l’onde qui guide le temps à plusieurs jours d’échéance, et la pression en surface montre où elle finit par s’ancrer.',
    interpretation: [
      'Les valeurs élevées sont une dorsale — air chaud et colonne dilatée, temps stable — et les valeurs basses, un thalweg ou une dépression d’altitude. Ce qui compte n’est pas tant la valeur que la forme : où l’onde se courbe et vers où elle avance.',
      'Les isobares se lisent par-dessus, pas séparément. Un minimum de géopotentiel juste au-dessus d’une dépression de surface est un système mature et vertical, avec peu de marge d’évolution ; déplacé à l’ouest de celle-ci, c’est un système encore en développement.',
      'Le gradient entre isohypses est proportionnel au vent à 500 hPa : là où elles se resserrent se trouve le courant-jet, et avec lui la bande par laquelle voyagent les dépressions.',
      'À +144 h, la carte n’est pas une prévision de détail mais de configuration. Elle sert à voir si une dorsale s’installe ou si un thalweg arrive, pas à décider à quelle heure il pleuvra.'
    ],
    method: 'Deux variables des données ouvertes du CEPMMT pour chaque échéance : l’altitude géopotentielle à 500 hPa, convertie de mètres géopotentiels en décamètres, et la pression au niveau de la mer, convertie de pascals en hectopascals. Les deux sont découpées au domaine euro-atlantique.',
    equations: [
      { label: 'Altitude géopotentielle en décamètres', latex: String.raw`Z_{500}[\mathrm{dam}]=\frac{Z_{500}[\mathrm{gpm}]}{10}` },
      { label: 'Pression au niveau de la mer', latex: String.raw`p_{\mathrm{mar}}[\mathrm{hPa}]=\frac{p_{\mathrm{mar}}[\mathrm{Pa}]}{100}` }
    ],
    steps: [
      'Variables du CEPMMT : altitude géopotentielle (gh) à 500 hPa et pression au niveau de la mer (msl).',
      'Conversion d’unités : de mètres géopotentiels en décamètres et de pascals en hectopascals.',
      'Découpage à la fenêtre euro-atlantique, sans lisser le champ de couleurs.'
    ],
    sources: [ECMWF_OPEN_DATA]
  },

  'temperature-2m': {
    what: 'Température de l’air prévue à 2 m au-dessus de la surface du modèle. C’est le champ de référence pour décrire l’ambiance thermique près du sol, mais elle n’équivaut pas à la température de peau du terrain.',
    interpretation: [
      'Les maximums et minimums permettent de suivre le réchauffement diurne, le refroidissement nocturne, les gelées et les épisodes de chaleur. Des gradients resserrés délimitent souvent des brises, des fronts, des inversions ou des contrastes terre-mer.',
      'La topographie, l’occupation du sol et le mélange de la couche limite conditionnent beaucoup ce champ. Vallées étroites, versants, cœurs urbains et lacs d’air froid peuvent différer d’une maille de 2,5 km ; il faut l’interpréter comme une température représentative de la maille, pas comme une lecture de station.'
    ],
    method: 'AROME publie la température à 2 m au-dessus du terrain. MeteoLabX sélectionne l’heure valide, conserve la grille et convertit en degrés Celsius quand la donnée arrive en kelvin.',
    equations: [
      { label: 'Conversion appliquée quand l’unité native est le kelvin', latex: String.raw`T_{2\,\mathrm m}[{}^\circ\mathrm C]=T_{2\,\mathrm m}[\mathrm K]-273.15` }
    ],
    steps: [
      'Variable d’AROME : TEMPERATURE à 2 m au-dessus du terrain.',
      'Aucune interpolation entre les heures ni lissage de la grille.',
      'La palette modifie seulement la représentation, jamais la valeur consultée dans la maille.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'vv-lfc': {
    what: 'Vitesse verticale du modèle au niveau de convection libre de la particule de couche mélangée, en m/s, positive vers le haut. Par-dessus, en lignes de courant, le vent en surface (10 m), qui montre où l’air converge et ce qui peut forcer l’ascendance.',
    interpretation: [
      'Les cartes de CAPE indiquent combien d’énergie est disponible, mais pas si quelque chose va la libérer. Cette carte montre si l’air ascendant atteint le niveau où la particule commence à monter d’elle-même, le NCL. S’il l’atteint, la convection peut se déclencher ; si l’ascendance s’arrête en dessous, généralement sous une inversion, l’énergie reste inutilisée.',
      'AROME représente les orages de façon explicite : là où le modèle en a déjà développé un, la valeur élevée au NCL est le courant ascendant de l’orage lui-même, et non le forçage qui le déclenche. Dans ces cas, la carte indique où il y a de la convection dans le modèle, mais pas ce qui l’a provoquée.',
      'Les lignes de courant sont le vent à 10 m, pas celui du niveau que la carte colore : elles montrent ce qui force l’ascendance — brise, ligne de convergence, relief — et vers où se propagerait ce qui se déclencherait.',
      'Là où les lignes de courant se rejoignent ou se heurtent, il y a convergence en surface, et c’est là qu’il faut regarder si la couleur indique que l’ascendance atteint le NCL. En montagne, le vent qui souffle contre un versant force aussi l’air à monter : la couleur peut donc marquer une ascendance par forçage orographique même là où les lignes de courant ne convergent pas.'
    ],
    method: 'Vitesse verticale géométrique d’AROME sur les niveaux de pression, interpolée linéairement à l’altitude du NCL. Le NCL provient de la particule de couche mélangée des 100 hPa inférieurs, la même qui sert à calculer la MLCAPE.',
    equations: [
      { label: 'Interpolation au NCL', latex: String.raw`w_{\mathrm{NCL}}=w_k+\frac{z_{\mathrm{NCL}}-z_k}{z_{k+1}-z_k}\,(w_{k+1}-w_k)` }
    ],
    steps: [
      'Vitesse verticale géométrique d’AROME sur les niveaux de pression.',
      'Altitude du niveau de convection libre de la particule ML100, au-dessus du terrain.',
      'Pas de valeur là où la particule ne gagne jamais de flottabilité : il n’y a pas de niveau où regarder.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'updraft-helicity': {
    what: 'Hélicité du courant ascendant entre 2 et 5 km au-dessus du terrain, en m²/s². Elle diagnostique la rotation que le modèle lui-même génère à l’intérieur d’un courant ascendant : elle combine la vitesse verticale avec la vorticité verticale entre 2 000 et 5 000 mètres au-dessus du sol.',
    interpretation: [
      'Des valeurs positives signifient que l’ascendance et la rotation cyclonique coïncident, ce qui dans l’hémisphère nord est la signature habituelle d’une supercellule déviée à droite. Des valeurs négatives, une rotation anticyclonique accompagnant l’ascendance : cela peut correspondre à une supercellule déviée à gauche, mais le signe seul ne le prouve pas.',
      'Elle mesure la coïncidence de l’ascendance et de la rotation, pas l’intensité de chacune : c’est le produit des deux, donc si l’une manque — un courant ascendant fort sans rotation, ou une zone en rotation sans ascendance — le résultat est nul ou presque nul.',
      'Une UH élevée identifie un mésocyclone simulé de niveaux moyens. Cela ne signifie pas automatiquement une tornade ni un temps violent en surface, aussi convient-il de la lire avec la MLCAPE, l’hélicité relative à l’orage, le cisaillement de couche effective, la vitesse verticale au NCL et l’évolution d’heure en heure.',
      'À la différence de la CAPE ou du cisaillement, elle ne décrit pas l’environnement mais ce que le modèle est en train de produire : elle apparaît là où AROME a déjà développé l’orage, pas avant. C’est pourquoi elle complète les champs d’environnement au lieu de les remplacer.',
      'MeteoLabX calcule l’UH à un seul instant, l’heure de sortie, à partir des niveaux de pression publiés. Un mésocyclone simulé dure peu et se déplace : il peut donc se produire entre deux heures sans être capté, et l’espacement entre niveaux lisse les pics. C’est pourquoi ses valeurs sont généralement plus basses que celles de la littérature, qui utilise le maximum horaire calculé par le modèle lui-même, et il vaut mieux les lire de façon relative : comparer les zones et les heures entre elles, pas avec des seuils fixes.'
    ],
    method: 'À chaque niveau de pression, on calcule la vorticité verticale, c’est-à-dire à quel point le vent tourne à l’horizontale, et on la multiplie par la vitesse verticale. Ces produits sont additionnés entre 2 000 et 5 000 m au-dessus du terrain, en interpolant les valeurs exactement à ces deux hauteurs.',
    equations: [
      { label: 'Vorticité verticale', latex: String.raw`\zeta=\frac{\partial v}{\partial x}-\frac{\partial u}{\partial y}` },
      { label: 'Hélicité de l’ascendance', latex: String.raw`\mathrm{UH}_{2-5}=\int_{2000}^{5000} w\,\zeta\;\mathrm{d}z` }
    ],
    steps: [
      'Pression, température, humidité, vent horizontal et vitesse verticale géométrique des niveaux de pression d’AROME.',
      'Altitude de chaque niveau reconstruite avec l’équation hypsométrique, moins le terrain : la couche se situe au-dessus du sol, pas au-dessus de la mer.',
      'Vorticité verticale dans le plan, avec les degrés convertis en mètres et la distance longitudinale corrigée par le cosinus de la latitude.',
      'Produit de la vitesse verticale par la vorticité à chaque niveau, interpolé exactement à 2 000 et 5 000 m.',
      'Intégration par trapèzes de tous les segments compris dans cette couche.',
      'Pas de valeur là où la colonne ne couvre pas la couche entière ou où il manque l’un des niveaux intermédiaires : une valeur partielle se lirait comme une rotation faible alors qu’il s’agit d’un manque de données.'
    ],
    sources: [MF_AROME, MF_API, NAYLOR_2012]
  },

  reflectivity: {
    what: 'Réflectivité maximale simulée de la colonne, en dBZ : la lecture que donnerait un radar si les hydrométéores du modèle étaient réels. De tous les niveaux, on retient la valeur la plus élevée, ce qu’un radar voit en balayant.',
    interpretation: [
      'C’est la carte la plus directe pour voir où il pleut et comment : elle ne donne pas une quantité cumulée mais la structure du moment, et l’on y distingue une bande stratiforme d’un train de cellules ou d’une ligne organisée.',
      'À titre indicatif : en dessous de 20 dBZ, pluie faible ou bruine ; entre 20 et 35, pluie modérée ; au-dessus de 40, il y a convection, et au-dessus de 50, des noyaux avec grêle possible. Ce sont les mêmes ordres de grandeur que sur un vrai radar, mais calculés, non mesurés.',
      'AROME résout partiellement la convection, si bien que la position exacte de chaque cellule est indicative : ce qui est fiable, c’est le type de structure et la zone, pas que le noyau tombe sur tel village précis.',
      'Aucune couleur en dessous de 5 dBZ. Sur une heure ordinaire, neuf dixièmes du domaine n’ont pas d’écho, et les colorer masquerait le peu qui compte.'
    ],
    method: 'Champ natif d’AROME, sans calcul propre : MeteoLabX se contente de le découper au domaine et de le servir. L’échelle procède par classes de 5 dBZ, comme celle d’un radar, plutôt que par dégradé continu.',
    equations: [],
    steps: [
      'Variable d’AROME : REFLECTIVITY_MAX_DBZ, une par heure.',
      'Classes de 5 en 5 dBZ jusqu’à 70 ; en dessous de 5, rien n’est coloré.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'mslp-theta-e-850': {
    what: 'Température potentielle équivalente à 850 hPa, en °C, avec la pression au niveau de la mer en isobares et ses centres marqués. La thêta-e résume en un seul chiffre la chaleur et l’humidité que transporte l’air, et elle se conserve que la masse monte à sec ou qu’elle condense : c’est pourquoi elle identifie la masse elle-même et non le thermomètre d’un instant donné, et permet d’identifier les fronts qui séparent les masses les unes des autres.',
    interpretation: [
      'C’est la carte des masses d’air. Une langue de thêta-e élevée qui avance sur des valeurs plus basses est une advection chaude et humide.',
      'Elle sert à identifier les fronts : ils se situent là où la thêta-e se resserre dans une bande étroite entre une masse chaude et humide et une autre froide ou sèche, et le front en surface longe généralement le bord chaud de cette bande. Si la bande avance vers l’air chaud, c’est un front froid ; si elle recule devant lui, c’est un front chaud. Les isobares aident à le confirmer, car elles fléchissent dans le thalweg qui accompagne le front. La thêta-e le marque mieux que la seule température, car une masse sèche et une masse humide peuvent afficher le même degré sans être la même chose.',
      'Elle se lit avec les isobares : l’air circule presque parallèlement à elles, si bien qu’elles indiquent d’où vient la masse que décrit la thêta-e. Une dépression à l’ouest avec des isobares de sud amène la langue chaude par l’avant.',
      'Le niveau de 850 hPa est choisi parce qu’il se situe au-dessus du frottement de surface et du cycle diurne, tout en restant dans l’air qui alimente la convection.',
      'Pas de valeur là où la pression en surface n’atteint pas 850 hPa : ce niveau est alors sous terre et le modèle publie une extrapolation qui n’est l’air de nulle part. C’est ce qui laisse en blanc les Alpes et une bonne partie du plateau espagnol.'
    ],
    method: 'Thêta-e de Bolton (1980), calculée avec MetPy sur la température et le point de rosée d’AROME à 850 hPa. Dans la réalité, le point de rosée ne dépasse jamais la température, mais le modèle le donne parfois quelques dixièmes au-dessus à cause de petites erreurs de calcul ; dans ces mailles, il est ramené à la température. La pression est celle du niveau lui-même, 850 hPa, la même sur toute la carte, et le calcul se fait en kelvin : seul le résultat est converti en degrés Celsius. Les isobares proviennent de la pression au niveau de la mer de la même échéance.',
    equations: [
      { label: 'Pression de vapeur', latex: String.raw`e=6{,}112\exp\!\left(\frac{17{,}67\,T_d}{T_d+243{,}5}\right)` },
      { label: 'Rapport de mélange', latex: String.raw`r=\frac{0{,}622\,e}{p-e}` },
      { label: 'Température du NCA', latex: String.raw`T_L=\left[\frac{1}{T_d-56}+\frac{\ln(T/T_d)}{800}\right]^{-1}+56` },
      { label: 'Thêta-e', latex: String.raw`\theta_e=T\left(\frac{1000}{p-e}\right)^{\kappa}\left(\frac{T}{T_L}\right)^{0{,}28r}\exp\!\left[\left(\frac{3036}{T_L}-1{,}78\right)r(1+0{,}448r)\right]` }
    ],
    steps: [
      'Variables d’AROME : température et point de rosée à 850 hPa, pression en surface et pression au niveau de la mer.',
      'Là où le modèle donne un point de rosée au-dessus de la température, il est ramené à celle-ci ; la pression est celle de 850 hPa dans toutes les mailles.',
      'Thêta-e calculée avec MetPy, qui applique la formulation de Bolton (1980), en kelvin ; seul le résultat est converti en degrés Celsius.',
      'Isobares tous les 4 hPa sur le champ non lissé, avec une sur cinq étiquetée.',
      'Dépressions et anticyclones : chaque centre doit être l’extremum de pression dans un rayon de 200 km et être fermé, c’est-à-dire entouré de pressions au moins 0,6 hPa plus hautes — ou plus basses, pour un anticyclone — sur un rayon de 100 km ou plus. Les centres principaux (A pour les anticyclones, B pour les dépressions) sont ceux qu’enferme une isobare de la carte ou un écart de 3 hPa sur au moins 150 km de rayon ; les autres sont des centres relatifs (a et b). Deux centres du même type à moins de 300 km comptent comme un seul, et le zoom ne change pas ceux qui apparaissent.'
    ],
    sources: [MF_AROME, MF_API, BOLTON_1980, METPY]
  },

  'srh-01': {
    what: 'Hélicité relative à l’orage entre le sol et 1 000 m au-dessus du terrain, en m²/s². Elle mesure la quantité de rotation qu’un orage peut capter du vent qui l’entoure.',
    interpretation: [
      'C’est la couche la plus associée à la tornadogenèse. Des valeurs supérieures à 100 m²/s² sont déjà favorables et au-delà de 150 elles sont significatives, toujours accompagnées d’instabilité et d’une base nuageuse basse.',
      'Le signe importe : positif indique une rotation cyclonique, négatif une rotation anticyclonique. Une SRH élevée sans CAPE décrit un environnement cisaillé mais sans orages ; à lire avec les cartes de CAPE et le cisaillement 0–6 km.',
      'Les flèches représentent le mouvement estimé de la supercellule déviée à droite, pas le vent : elles indiquent vers où se déplacerait l’orage auquel cette hélicité se rapporte.'
    ],
    method: 'Intégrale de l’hodographe entre 0 et 1 000 m AGL, en soustrayant le mouvement de Bunkers 2000 right mover. Tous les niveaux du profil sont parcourus, pas seulement les extrémités, et la limite supérieure est interpolée. Les hauteurs sont au-dessus du terrain et les niveaux isobares souterrains sont exclus. Il n’y a pas de filtre par CAPE : c’est un champ cinématique de l’environnement.',
    equations: [
      { label: 'Hélicité relative', latex: String.raw`\mathrm{SRH}=\sum_i\left[(u_{i+1}-C_u)(v_i-C_v)-(u_i-C_u)(v_{i+1}-C_v)\right]` },
      { label: 'Mouvement de Bunkers', latex: String.raw`\mathbf{C}_R=\overline{\mathbf{V}}_{0-6}+7{,}5\,\frac{(\Delta v,\,-\Delta u)}{|\Delta \mathbf{V}|}` }
    ],
    steps: [
      'Profil de vent des niveaux de pression d’AROME, avec le vent à 10 m comme base.',
      'Mouvement de Bunkers : vent moyen 0–6 km dévié de 7,5 m/s perpendiculairement au cisaillement entre les couches 0–0,5 et 5,5–6 km.',
      'Les moyennes sont pondérées par l’épaisseur et non par la pression, comme le précise Bunkers et al. (2000) : il y est montré que pondérer par la pression ne réduit pas l’erreur. MetPy intègre ces mêmes couches en coordonnée de pression, si bien que son mouvement ressort un peu différent — 1,1 m/s par composante dans un hodographe de test — sans qu’aucune des deux formulations soit fausse.',
      'Pas de valeur là où le profil n’atteint pas 6 km : la déviation de Bunkers reste indéfinie.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'srh-03': {
    what: 'Hélicité relative à l’orage entre le sol et 3 000 m au-dessus du terrain, en m²/s². Même grandeur que celle de 0–1 km, sur la couche qui englobe l’essentiel du courant ascendant.',
    interpretation: [
      'C’est la couche habituelle pour évaluer le potentiel de rotation d’une supercellule. Au-delà de 150 m²/s², l’environnement favorise les supercellules, et au-delà de 300, la rotation est marquée.',
      'Comparer 0–3 à 0–1 km indique où se trouve la rotation : si celle de 0–1 km est proportionnellement élevée, le cisaillement se concentre près du sol, configuration associée aux tornades.',
      'Les flèches représentent le mouvement estimé de la supercellule déviée à droite, le même que l’on soustrait pour calculer l’hélicité.'
    ],
    method: 'Intégrale de l’hodographe entre 0 et 3 000 m AGL, en soustrayant le mouvement de Bunkers 2000 right mover. Tous les niveaux du profil sont parcourus, pas seulement les extrémités, et la limite supérieure est interpolée. Les hauteurs sont au-dessus du terrain et les niveaux isobares souterrains sont exclus. Il n’y a pas de filtre par CAPE : c’est un champ cinématique de l’environnement.',
    equations: [
      { label: 'Hélicité relative', latex: String.raw`\mathrm{SRH}=\sum_i\left[(u_{i+1}-C_u)(v_i-C_v)-(u_i-C_u)(v_{i+1}-C_v)\right]` },
      { label: 'Mouvement de Bunkers', latex: String.raw`\mathbf{C}_R=\overline{\mathbf{V}}_{0-6}+7{,}5\,\frac{(\Delta v,\,-\Delta u)}{|\Delta \mathbf{V}|}` }
    ],
    steps: [
      'Profil de vent des niveaux de pression d’AROME, avec le vent à 10 m comme base.',
      'Mouvement de Bunkers : vent moyen 0–6 km dévié de 7,5 m/s perpendiculairement au cisaillement entre les couches 0–0,5 et 5,5–6 km.',
      'Les moyennes sont pondérées par l’épaisseur et non par la pression, comme le précise Bunkers et al. (2000). MetPy intègre ces mêmes couches en pression et obtient un mouvement un peu différent ; l’hélicité, partant du même mouvement, coïncide entre les deux jusqu’au dernier chiffre.',
      'Pas de valeur là où le profil n’atteint pas 6 km : la déviation de Bunkers reste indéfinie.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'vertical-totals': {
    what: 'Vertical Totals : différence de température entre les surfaces isobares de 850 et 500 hPa. Il indique à quel point l’air se refroidit en prenant de l’altitude entre environ 1 500 et 5 500 m : plus la différence est grande, plus la colonne est instable.',
    interpretation: [
      'Il décrit l’environnement, pas une particule précise. C’est là son intérêt aux côtés des CAPE : là où MUCAPE et MLCAPE divergent parce que la couche source est incertaine, le VT n’a pas cette ambiguïté, car il ne dépend pas de la particule choisie.',
      'Des valeurs autour de 26 °C indiquent un gradient suffisant pour la convection ; au-delà de 30 °C le gradient est marqué. Un VT élevé avec peu d’humidité en basses couches pointe vers un environnement de rafales descendantes sèches, où l’air descendant se refroidit peu par évaporation mais accélère par le gradient.',
      'Il n’intègre pas l’humidité : à lui seul, il ne distingue pas une atmosphère instable et humide d’une instable et sèche. À lire avec l’humidité des basses couches ou la DCAPE.'
    ],
    method: 'Soustraction directe de la température d’AROME à deux niveaux de pression, 850 et 500 hPa.',
    equations: [
      { label: 'Vertical Totals', latex: String.raw`\mathrm{VT}=T_{850}-T_{500}` }
    ],
    steps: [
      'Variable d’AROME : TEMPERATURE à 850 et 500 hPa.',
      'Différence en kelvin, équivalente à la différence en degrés Celsius.',
      'Pas de valeur là où p_s < 850 hPa : la surface isobare est sous terre.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'temperature-850': {
    what: 'Température de l’air sur la surface isobare de 850 hPa. Elle représente la masse d’air de la basse troposphère, généralement au-dessus de l’essentiel de l’influence thermique immédiate du sol.',
    interpretation: [
      'Elle est utile pour suivre les advections chaudes ou froides et comparer les masses d’air. Un gradient marqué associé au vent à 850 hPa signale un transport thermique, mais ne détermine pas à lui seul la température à 2 m.',
      'Combinée à l’humidité, à l’épaisseur et au profil vertical, elle aide à évaluer la limite pluie-neige ou la stabilité. Là où la pression de surface est inférieure à 850 hPa — relief élevé —, la surface isobare est sous terre et la valeur n’a pas d’interprétation atmosphérique physique.'
    ],
    method: 'Température d’AROME au niveau de 850 hPa. MeteoLabX se contente de sélectionner le niveau et de convertir le kelvin en Celsius quand c’est nécessaire.',
    equations: [
      { label: 'Conversion d’unité', latex: String.raw`T_{850}[{}^\circ\mathrm C]=T_{850}[\mathrm K]-273.15` }
    ],
    steps: [
      'Variable d’AROME : TEMPERATURE sur les niveaux de pression.',
      'Niveau : 850 hPa.',
      'À ignorer ou masquer là où p_s < 850 hPa.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'temperature-500': {
    what: 'Température de l’air à 500 hPa, une référence de la troposphère moyenne située approximativement entre 5 et 6 km d’altitude selon l’état de la colonne.',
    interpretation: [
      'Les gouttes froides, les thalwegs et les dépressions d’altitude apparaissent comme des minimums thermiques. Un air plus froid à 500 hPa au-dessus d’une couche basse chaude et humide augmente le gradient thermique vertical et peut accroître la flottabilité.',
      'Ce n’est pas une carte d’orages : la convection nécessite simultanément humidité, instabilité de particule, forçage et un environnement de vent adéquat. Il convient aussi d’analyser le géopotentiel et l’advection, pas seulement la température.'
    ],
    method: 'Température d’AROME au niveau de 500 hPa. MeteoLabX conserve la donnée de la maille et l’exprime en degrés Celsius.',
    equations: [
      { label: 'Conversion d’unité', latex: String.raw`T_{500}[{}^\circ\mathrm C]=T_{500}[\mathrm K]-273.15` }
    ],
    steps: [
      'Variable d’AROME : TEMPERATURE sur les niveaux de pression.',
      'Niveau : 500 hPa.',
      'Ni CAPE ni gradient vertical ne se déduisent de cette carte isolée.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'freezing-level': {
    what: 'Altitude au-dessus du niveau de la mer à laquelle la température de l’air franchit 0 °C, ce qu’on appelle l’isotherme 0 °C ou niveau de congélation. Au-dessus, l’eau des nuages tend à geler et les flocons se conservent ; en dessous, ce qui tombe commence à fondre. C’est la référence thermique la plus directe pour savoir jusqu’où descend l’air froid dans la verticale, et le point de départ pour estimer la limite pluie-neige.',
    interpretation: [
      'Elle sert à suivre l’arrivée et le retrait des masses d’air : un isotherme 0 °C qui descend de 3 000 à 1 200 m en une journée signale une irruption froide, et un qui monte au-dessus de 4 000 m en été, une masse très chaude. En hiver, avec l’isotherme 0 °C à 1 500 m, les montagnes au-dessus de cette altitude sont sous zéro même si les vallées sont douces.',
      'Ce n’est pas la limite pluie-neige. Les flocons ne fondent pas dès qu’ils franchissent 0 °C : cela prend quelques centaines de mètres, et si l’air est sec, l’évaporation les refroidit et ils tiennent encore plus bas. C’est pourquoi la neige atteint généralement 300 à 600 m sous l’isotherme 0 °C, et davantage encore avec de l’air sec. C’est le rôle de la carte de limite pluie-neige.',
      'Si en surface il fait déjà sous zéro et qu’il n’y a pas de couche plus douce au-dessus, l’isotherme 0 °C se situe au ras du sol et la carte montre l’altitude du terrain du modèle lui-même. Ce terrain est lissé : les vallées étroites et les sommets apparaissent plus bas ou plus hauts qu’ils ne le sont en réalité.',
      'Une même colonne peut franchir 0 °C plus d’une fois. Cela arrive surtout avec les inversions thermiques : un lac d’air froid dans une vallée ou une plaine, sous zéro, avec une couche plus tiède au-dessus qui se refroidit de nouveau plus haut. Il y a alors plusieurs isothermes 0 °C superposés et la carte affiche le plus élevé, celui qui marque où commence à fondre ce qui tombe. En activant la couche « Zones à solutions multiples », on voit les mailles où cela se produit ; il faut alors savoir qu’en dessous existe un autre isotherme 0 °C, parfois proche du sol, que la couleur ne montre pas.',
      'Elle est représentée en altitude, pas en hauteur au-dessus du terrain : 1 500 m signifient la même chose sur la côte que dans les Pyrénées. D’autres indices, comme SHIP, utilisent la hauteur au-dessus du sol.'
    ],
    method: 'MeteoLabX construit le profil vertical de température de chaque maille en reliant la température à 2 m à celle des niveaux de pression d’AROME, chacun à son altitude réelle calculée à partir du géopotentiel. Il le parcourt ensuite de bas en haut et, chaque fois que la température passe d’un côté à l’autre de 0 °C entre deux niveaux, interpole en ligne droite l’altitude exacte du passage.',
    equations: [
      { label: 'Altitude de chaque niveau à partir du géopotentiel', latex: String.raw`z_i=\frac{\Phi_i}{g_0}` },
      { label: 'Passage interpolé entre deux niveaux consécutifs', latex: String.raw`z_{0}=z_i+\frac{0-T_i}{T_{i+1}-T_i}\,\left(z_{i+1}-z_i\right)` }
    ],
    steps: [
      'Ancrer le profil au sol. Le premier point est la température à 2 m, situé à l’altitude du terrain du modèle plus ces 2 m.',
      'Ajouter les niveaux d’altitude. On additionne les niveaux de pression qui se trouvent au-dessus du terrain ; ceux qui tombent sous terre en zone de montagne sont écartés.',
      'Chercher les passages. Les niveaux sont comparés deux à deux, de bas en haut, et chaque passage par 0 °C est interpolé linéairement. La précision dépend de l’écart entre niveaux, de quelques centaines de mètres : le passage est exact si la température change de façon régulière entre eux.',
      'Choisir la solution. S’il n’y a qu’un passage, c’est la valeur retenue. S’il y en a plusieurs, on affiche le plus élevé et la maille est marquée comme zone à solutions multiples. Si toute la colonne est sous zéro depuis le sol, la valeur est l’altitude du terrain.',
      'Laisser sans donnée ce qui est douteux. S’il manque un niveau intermédiaire, aucun passage n’est inventé à travers le vide et la maille reste vide.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'snow-level': {
    what: 'Altitude approximative au-dessus de laquelle la précipitation prévue tomberait sous forme de neige. MeteoLabX la situe là où la température du thermomètre mouillé de l’air franchit 0,5 °C. Le thermomètre mouillé est la température à laquelle se refroidit l’air quand on y évapore de l’eau jusqu’à saturation, et c’est ce que vit réellement un flocon en tombant : pendant sa descente il fond sous l’effet de la chaleur de l’air, mais en même temps il perd de l’eau par évaporation et sublimation, ce qui le refroidit. C’est pourquoi, avec de l’air sec, il neige à des altitudes bien plus basses que ne le suggérerait la seule température. La carte n’a de valeur que là où le modèle prévoit de la précipitation à cette heure.',
    interpretation: [
      'C’est la transition de la pluie à la neige, pas une ligne exacte. Environ 200 à 300 m au-dessus de la limite, il neige généralement sans problème ; autour d’elle, il est habituel d’avoir de la pluie et neige mêlées ou de la neige humide qui tient difficilement, et en dessous, de la pluie.',
      'À comparer avec l’isotherme 0 °C : si la limite se situe bien en dessous, l’air est sec et l’évaporation refroidit la précipitation. Quand il pleut de façon persistante, cet air s’humidifie peu à peu et la limite peut monter au fil de l’épisode.',
      'Avec une précipitation intense, la limite réelle peut descendre plus bas que prévu : en fondant, les flocons prennent de la chaleur à l’air et le refroidissent, et dans des vallées encaissées cet air froid s’accumule. C’est un processus que le profil d’une seule maille ne capte qu’en partie.',
      'Il peut y avoir plus d’une solution. Avec une inversion thermique, le thermomètre mouillé franchit 0,5 °C plusieurs fois : par exemple, de l’air froid collé au sol, une couche plus tiède au-dessus et de l’air froid de nouveau plus haut. La carte affiche le passage le plus élevé, où la neige commence à fondre, mais en dessous de la couche tiède l’air redevient froid et la précipitation peut atteindre le sol sous forme de pluie et neige mêlées, de neige ou même de pluie verglaçante. La couche « Zones à solutions multiples » signale ces mailles : là, la couleur ne suffit pas et il vaut mieux regarder le profil complet.',
      'Là où il n’y a pas de précipitation, la carte reste vide : sans rien qui tombe, parler de limite pluie-neige n’a pas de sens. Si toute la colonne est en dessous du seuil depuis le sol, la valeur est l’altitude du terrain du modèle, c’est-à-dire de la neige jusqu’au sol.',
      'C’est une altitude au-dessus du niveau de la mer, fondée sur un terrain lissé, si bien qu’au fond des vallées et sur les versants abrupts elle peut différer de ce qui est observé.'
    ],
    method: 'MeteoLabX calcule la température du thermomètre mouillé en surface et à chaque niveau de pression d’AROME à partir de la température, de l’humidité et de la pression. Avec ce profil, il cherche, de bas en haut, l’altitude à laquelle le thermomètre mouillé franchit 0,5 °C, en interpolant entre les niveaux. Le seuil de 0,5 °C plutôt que 0 °C tient compte du fait que les flocons ne fondent pas instantanément : ils doivent traverser un peu d’air au-dessus de zéro avant de se transformer en pluie.',
    equations: [
      { label: 'Équation psychrométrique définissant le thermomètre mouillé', latex: String.raw`e_s(T_w)-\gamma\,(T-T_w)=e,\qquad \gamma=\frac{c_p\,p}{0.622\,L_v}` },
      { label: 'Pression de vapeur saturante', latex: String.raw`e_s(T)=6.112\,\exp\!\left(\frac{17.67\,T}{T+243.5}\right)` },
      { label: 'Passage interpolé entre deux niveaux consécutifs', latex: String.raw`z_{T_w=0.5}=z_i+\frac{0.5-T_{w,i}}{T_{w,i+1}-T_{w,i}}\,\left(z_{i+1}-z_i\right)` }
    ],
    steps: [
      'Obtenir l’humidité de chaque niveau. En surface, on utilise le point de rosée à 2 m ; en altitude, le point de rosée est déduit de l’humidité relative et de la température.',
      'Calculer le thermomètre mouillé. L’équation psychrométrique est résolue par approximations successives avec la pression réelle de chaque niveau, car le refroidissement par évaporation en dépend. Le résultat se situe toujours entre le point de rosée et la température.',
      'Construire le profil. Le premier point est la surface, à l’altitude du terrain du modèle plus 2 m ; au-dessus viennent les niveaux de pression, chacun avec son altitude tirée du géopotentiel. Les niveaux situés sous terre sont écartés.',
      'Chercher les passages à 0,5 °C. La colonne est parcourue de bas en haut et chaque passage par le seuil est interpolé linéairement. S’il y en a plusieurs, on affiche le plus élevé et la maille est marquée comme zone à solutions multiples.',
      'Filtrer par précipitation. La limite n’est affichée que là où la précipitation de cette heure atteint au moins 0,05 mm. S’il manque un niveau intermédiaire, la maille reste sans donnée plutôt que d’inventer un passage.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'wind-level': {
    what: 'Vent horizontal au niveau sélectionné. Les couleurs représentent la vitesse et les lignes de courant suivent la direction dans laquelle se déplace l’air.',
    interpretation: [
      'Aux niveaux au-dessus du terrain, elle permet de localiser les canalisations, les jets de basse altitude, les convergences et les accélérations orographiques. Aux niveaux isobares, elle montre la circulation synoptique et la position relative des dorsales, des thalwegs et des courants-jets.',
      'Le rapprochement ou l’écartement des lignes de courant suggère une convergence ou une divergence horizontale, mais ne la quantifie pas : cela demande de calculer des dérivées spatiales. À un niveau isobare, les mailles où p_s < p_niveau sont masquées, car ce niveau serait sous le terrain.'
    ],
    method: 'MeteoLabX prend les composantes U et V du vent d’AROME au niveau choisi, qu’il s’agisse d’une hauteur au-dessus du terrain ou d’un niveau de pression, et calcule la vitesse. Les lignes de courant suivent la direction de U et V et se densifient au zoom ; elles ne modifient pas le champ.',
    equations: [
      { label: 'Vitesse horizontale montrée en couleurs', latex: String.raw`|\vec V|=\sqrt{u^2+v^2}` },
      { label: 'Champ directionnel suivi par les lignes de courant', latex: String.raw`\frac{d\vec x}{ds}=\frac{\vec V(\vec x)}{|\vec V(\vec x)|}` }
    ],
    steps: [
      'Variables d’AROME : composantes U et V du vent au niveau choisi.',
      'Vitesse calculée maille par maille à partir de U et V.',
      'Aux niveaux de pression, seules les mailles où le niveau se trouve au-dessus du terrain sont montrées (p_s ≥ p_niveau).',
      'L’orientation est celle du flux météorologique ; ce ne sont pas des trajectoires temporelles de particules.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'wind-gust': {
    what: 'Rafale de vent maximale à 10 m prévue dans l’heure se terminant à l’heure valide de la carte.',
    interpretation: [
      'Elle met en évidence de brefs maximums que le vent moyen ne montre pas : passages frontaux, mélange turbulent, canalisation par le relief et rafales convectives possibles.',
      'C’est un maximum infrahoraire paramétré par le modèle, pas une vitesse soutenue ni une observation. Dans les petits orages, il peut y avoir des erreurs de position et d’intensité ; il convient de la comparer à la DCAPE, à la précipitation, à la réflectivité et à l’évolution des cellules.'
    ],
    method: 'Rafale maximale d’AROME (WIND_SPEED_GUST_MAX) à 10 m durant l’heure précédente. MeteoLabX ne reconstruit pas la rafale : elle montre directement le maximum publié pour cet intervalle.',
    equations: [
      { label: 'Signification temporelle du champ', latex: String.raw`G_{1h}(t)=\max_{\tau\in(t-1\,h,\,t]}|\vec V_{10m}(\tau)|` }
    ],
    steps: [
      'Variable d’AROME : WIND_SPEED_GUST_MAX à 10 m.',
      'Niveau : 10 m ; période : l’heure précédente.',
      'Le visualiseur conserve les m/s ; la palette ne modifie pas le maximum.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'shear-01': {
    what: 'Le cisaillement vectoriel 0–1 km représente le changement du vecteur vent entre la surface et 1 km d’altitude. Sa magnitude indique combien le vent change et sa direction montre vers où se produit ce changement.',
    interpretation: [
      'Des valeurs élevées de CIZ1 indiquent un cisaillement plus fort et donc une plus grande vorticité horizontale dans les basses couches, qui peut être basculée et transformée en rotation verticale par les courants ascendants. Cela favorise l’organisation convective et la rotation de bas niveau, en particulier dans les supercellules et les systèmes convectifs organisés.',
      'Dans les supercellules, un cisaillement fort et bien orienté par rapport au mouvement de la cellule peut augmenter la quantité de vorticité streamwise ingérée par le courant ascendant, augmentant la SRH 0–1 km et favorisant la rotation du mésocyclone de bas niveau.',
      'Dans les lignes d’orages, une composante de cisaillement de basses couches perpendiculaire à la ligne favorise généralement davantage son maintien, car elle peut compenser la circulation associée à la plage froide et maintenir les nouveaux courants ascendants près du front de rafales.',
      'Quand le cisaillement est plus parallèle à l’axe de la ligne, il favorise davantage la propagation et la régénération de cellules le long de l’axe même du système, plutôt que de renforcer la régénération frontale perpendiculaire à la ligne.',
      'CIZ approximativement perpendiculaire à la ligne : favorise une régénération plus frontale, près du bord d’attaque, et peut aider à maintenir une ligne compacte quand la circulation associée à la plage froide et le cisaillement ambiant sont raisonnablement équilibrés.'
    ],
    method: 'Diagnostic entièrement calculé par MeteoLabX à partir des composantes U/V natives d’AROME à 10 m et 1 000 m AGL pour la même heure. Les deux grilles sont alignées spatialement sur celle du niveau de 10 m et la différence vectorielle entre les deux niveaux est calculée.',
    equations: [
      { label: 'Vecteur de cisaillement 0–1 km', latex: String.raw`\Delta\vec V_{0-1}=\vec V_{1000\,m}-\vec V_{10\,m}` },
      { label: 'Magnitude représentée', latex: String.raw`\mathrm{CIZ}_{0-1}=\sqrt{(u_{1000}-u_{10})^2+(v_{1000}-v_{10})^2}` },
      { label: 'Relation approximative avec la vorticité horizontale', latex: String.raw`\vec\omega_h\approx\hat{k}\times\frac{\partial\vec V}{\partial z}` }
    ],
    steps: [
      'La direction des flèches correspond à l’orientation du vecteur différence, pas à la direction du vent ni au déplacement des orages.',
      'Les couleurs et la valeur affichée au pointeur conservent la résolution complète de la grille ; les flèches peuvent regrouper plusieurs mailles uniquement pour améliorer la lisibilité.',
      'Des valeurs élevées de CIZ0–1 indiquent un changement intense du vent dans le premier kilomètre de l’atmosphère et une plus grande disponibilité de vorticité horizontale.'
    ],
    sources: [RKW_1988, LINE_ORIENTATION, MF_API]
  },

  'shear-03': {
    what: 'Le cisaillement vectoriel 0–3 km représente le changement du vecteur vent entre la surface et 3 km d’altitude. Sa magnitude indique combien le vent change dans la basse troposphère et sa direction montre l’orientation de ce changement.',
    interpretation: [
      'Des valeurs élevées de CIZ3 indiquent une variation importante du vent durant les premiers 3 km de l’atmosphère et donc une plus grande disponibilité de vorticité horizontale. Elles favorisent une convection plus organisée, avec une plus grande capacité à maintenir des structures multicellulaires, des lignes convectives, des QLCS et, quand le profil complet est favorable, des supercellules.',
      'Dans les lignes d’orages, la composante de CIZ3 perpendiculaire à l’axe de la ligne est particulièrement importante. Un cisaillement perpendiculaire adéquat peut compenser la circulation générée par la plage froide et maintenir les nouveaux courants ascendants près du front de rafales, favorisant la persistance et l’organisation de la ligne. Cependant, un cisaillement croissant n’implique pas forcément une ligne de plus en plus intense : le maintien optimal dépend de l’équilibre entre le cisaillement ambiant et l’intensité de la plage froide.',
      'Quand CIZ3 est principalement parallèle à la ligne, elle contribue moins à l’équilibre frontal avec la plage froide. Elle peut influencer la propagation, la succession et la régénération de cellules le long de l’axe du système et la répartition de la précipitation, mais à elle seule elle ne détermine pas la direction de déplacement de la ligne.'
    ],
    method: 'Diagnostic entièrement calculé par MeteoLabX à partir des composantes U/V à 10 m et 3 000 m AGL pour la même heure. Après alignement spatial des deux grilles sur celle du niveau inférieur, la différence vectorielle entre les deux niveaux est calculée.',
    equations: [
      { label: 'Vecteur et magnitude', latex: String.raw`\Delta\vec V_{0-3}=\vec V_{3000\,m}-\vec V_{10\,m},\qquad \mathrm{CIZ}_{0-3}=|\Delta\vec V_{0-3}|` },
      { label: 'Composantes', latex: String.raw`|\Delta\vec V|=\sqrt{(\Delta u)^2+(\Delta v)^2}` }
    ],
    steps: [
      'La direction des flèches correspond à l’orientation du vecteur différence, pas à la direction du vent ni au déplacement des orages.',
      'Les couleurs et la valeur affichée au pointeur conservent la résolution complète de la grille ; les flèches peuvent regrouper plusieurs mailles uniquement pour améliorer la lisibilité.',
      'Des valeurs élevées de CIZ0–3 indiquent un changement intense du vent durant les premiers 3 km et une plus grande disponibilité de vorticité horizontale pour interagir avec les courants ascendants.'
    ],
    sources: [RKW_1988, LINE_ORIENTATION, MF_API]
  },

  'shear-06': {
    what: 'Le cisaillement vectoriel 0–6 km représente le changement du vecteur vent entre la surface et 6 km d’altitude. Sa magnitude mesure combien le vent change à travers une couche profonde de la troposphère et sa direction montre l’orientation de ce changement. C’est un indicateur fondamental de la capacité de l’environnement à organiser et à maintenir une convection profonde.',
    interpretation: [
      'Des valeurs élevées de CIZ6 favorisent la séparation spatiale entre le courant ascendant, le courant descendant et la précipitation, réduisant leur interférence et permettant des orages plus organisés et plus durables. Un CIZ6 faible est généralement associé à des cellules pulsantes ou peu persistantes ; des valeurs modérées ou fortes favorisent des multicellules organisées, des lignes convectives et des supercellules, à condition qu’il y ait suffisamment d’instabilité.',
      'Dans les supercellules, le cisaillement profond facilite le basculement de la vorticité horizontale et aide à maintenir un courant ascendant rotatif séparé de la précipitation. CIZ6 est particulièrement utile pour estimer l’organisation et la persistance de la convection profonde, mais ne garantit pas à lui seul qu’un orage devienne une supercellule.',
      'Un cisaillement profond intense favorise aussi la séparation des membres déviés à droite et à gauche lors des processus de scission de l’orage (storm splitting). La trajectoire et l’intensité de chaque membre dépendent de l’hodographe, du vent moyen et de sa propagation dynamique, pas de la direction de CIZ6 isolément.',
      'Dans les lignes convectives, la composante de CIZ6 perpendiculaire à l’axe de la ligne peut aider à séparer les courants ascendants de la précipitation et favoriser l’organisation profonde du système. Une composante plus parallèle influence davantage la propagation et la régénération de cellules le long de l’axe. Pour analyser spécifiquement l’équilibre entre la plage froide et le cisaillement au front de rafales, la couche 0–3 km est généralement plus représentative que le CIZ6 complet.'
    ],
    method: 'Diagnostic entièrement calculé par MeteoLabX à partir des composantes U/V natives d’AROME à 10 m et 6 000 m AGL pour la même heure. Après alignement spatial des deux grilles sur celle du niveau inférieur, la différence vectorielle entre les deux niveaux est calculée.',
    equations: [
      { label: 'Vecteur de cisaillement 0–6 km', latex: String.raw`\Delta\vec V_{0-6}=\vec V_{6000\,m}-\vec V_{10\,m}` },
      { label: 'Magnitude représentée', latex: String.raw`\mathrm{CIZ}_{0-6}=\sqrt{(u_{6000}-u_{10})^2+(v_{6000}-v_{10})^2}` },
      { label: 'Relation approximative avec la vorticité horizontale', latex: String.raw`\vec\omega_h\approx\hat{k}\times\frac{\partial\vec V}{\partial z}` }
    ],
    steps: [
      'La direction des flèches correspond à l’orientation du vecteur différence, pas à la direction du vent ni au déplacement des orages.',
      'Les couleurs et la valeur affichée au pointeur conservent la résolution complète de la grille ; les flèches peuvent regrouper plusieurs mailles uniquement pour améliorer la lisibilité.',
      'Le calcul n’intègre pas en lui-même la CAPE, le mouvement de l’orage, la SRH, la courbure de l’hodographe, la profondeur effective de l’orage ni l’intensité de la plage froide. CIZ6 doit s’interpréter avec ces facteurs.'
    ],
    sources: [LINE_ORIENTATION, MF_API, NOAA_CAPE]
  },

  ebwd: {
    what: 'L’EBWD (Effective Bulk Wind Difference) mesure combien le vent change, comme vecteur, dans la moitié inférieure de la profondeur effective de l’orage. Le calcul commence à la base de la couche d’entrée capable d’alimenter réellement la convection et se termine à mi-chemin vers le niveau d’équilibre de la particule la plus instable. C’est pourquoi elle n’utilise pas toujours une couche fixe de la surface à 6 km. Face à CIZ6, l’EBWD s’adapte à la fois à la hauteur de l’inflow et à la profondeur de l’orage. Cette différence est particulièrement utile dans la convection élevée et dans les orages plus peu profonds ou plus profonds qu’à l’accoutumée.',
    interpretation: [
      'Les couleurs expriment la magnitude de l’EBWD : plus la valeur est élevée, plus le changement du vent est important dans la couche effective. En présence d’instabilité et d’un mécanisme de déclenchement, une EBWD plus élevée favorise généralement des orages plus organisés et plus persistants, car elle aide à séparer le courant ascendant du courant descendant et de la précipitation.',
      'À titre de référence opérationnelle, l’environnement devient progressivement plus favorable aux supercellules quand l’EBWD entre approximativement dans l’intervalle de 25 à 40 kt ou le dépasse.',
      'La flèche d’EBWD montre l’orientation du changement du vent entre la base et le sommet effectifs ; ce n’est ni la direction du vent ni le mouvement de l’orage. Son effet dépend de l’orientation relative : perpendiculaire à une frontière, elle favorise la séparation des cellules qui restent plus discrètes, tandis que parallèle elle augmente leurs interactions et l’évolution linéaire. Dans une ligne, la composante perpendiculaire aide davantage à soutenir la régénération frontale et la parallèle transporte et réorganise les cellules le long de l’axe.',
      'Si aucune particule ne remplit les critères de CAPE et de CIN, la couche effective n’existe pas et l’EBWD reste indéfinie.'
    ],
    method: 'MeteoLabX calcule le diagnostic directement à partir du profil thermodynamique et U/V d’AROME, en suivant la définition de couche effective de Thompson et al. (2007) ; il n’appelle pas SHARPpy pour obtenir l’EBWD. Le résultat est sensible à la résolution verticale et à l’interpolation entre niveaux.',
    equations: [
      { label: 'Critère d’entrée effective', latex: String.raw`\mathrm{CAPE}_{parcela}\ge100\ \mathrm{J\,kg^{-1}},\qquad \mathrm{CIN}_{parcela}\ge-250\ \mathrm{J\,kg^{-1}}` },
      { label: 'Sommet de la couche EBWD', latex: String.raw`z_{top}=z_{base}+\tfrac12\left(z_{EL,MU}-z_{base}\right)` },
      { label: 'Vecteur effectif', latex: String.raw`\mathrm{EBWD}=\left|\vec V(z_{top})-\vec V(z_{base})\right|` }
    ],
    steps: [
      'Construire le profil. MeteoLabX combine la surface avec les niveaux isobares d’AROME pour obtenir température, humidité, hauteur et composantes U/V dans une même colonne.',
      'Trouver la base effective. On teste des particules de la surface jusqu’à 500 hPa. La première qui remplit CAPE ≥ 100 J/kg et CIN ≥ −250 J/kg fixe la base de la couche.',
      'Situer le sommet effectif. On prend le point médian, en hauteur, entre cette base et le niveau d’équilibre de la particule la plus instable.',
      'Calculer la différence vectorielle. U et V sont interpolés linéairement à la base et au sommet ; on soustrait ensuite le vecteur de la base à celui du sommet et on calcule son module.'
    ],
    sources: [THOMPSON_2007, SHARPPY]
  },

  'precip-1h': {
    what: 'C’est la précipitation totale accumulée durant l’heure immédiatement précédant l’heure valide de la carte. Elle inclut la pluie et toutes les autres phases de précipitation exprimées en équivalent d’eau liquide.',
    interpretation: [
      'La carte montre la quantité accumulée en une heure, pas la sévérité de l’orage ni son intensité instantanée. Le total dépend à la fois de la quantité précipitée par le système et du temps qu’il reste au-dessus de chaque point.',
      'C’est pourquoi une cellule relativement faible mais lente ou stationnaire peut laisser des accumulations horaires élevées. À l’inverse, un orage très intense ou violent qui se déplace rapidement peut produire de petites accumulations à chaque point, même s’il génère de la grêle, un vent fort ou une activité électrique intense.',
      'La précipitation convective présente une erreur importante de phase et de position. Une cellule prévue à quelques kilomètres de sa place réelle peut produire une grande erreur locale même si le schéma météorologique général est correct. La carte ne doit pas être interprétée comme une mesure ponctuelle exacte.',
      'Ce champ ne diagnostique pas la sévérité convective : il n’informe pas directement sur la grêle, les rafales, la foudre, la rotation ni l’organisation. Il ne distingue pas non plus la phase qui a atteint le sol, car toutes sont converties en équivalent liquide.'
    ],
    method: 'Précipitation totale d’AROME (TOTAL_PRECIPITATION) accumulée sur une heure. MeteoLabX sélectionne l’heure demandée, ramène à zéro toute valeur négative et applique l’équivalence d’eau 1 kg/m² = 1 mm.',
    equations: [
      { label: 'Accumulation sur l’intervalle', latex: String.raw`P_{1h}(t)=\int_{t-1h}^{t} R(\tau)\,d\tau` },
      { label: 'Équivalence d’eau', latex: String.raw`1\ \mathrm{kg\,m^{-2}}=1\ \mathrm{mm}` }
    ],
    steps: [
      'Sélectionner la variable. C’est TOTAL_PRECIPITATION accumulée sur l’heure.',
      'Attribuer l’intervalle. La carte valide à l’heure t représente exclusivement l’intervalle (t−1 h, t].',
      'Montrer l’accumulation. Les valeurs sont exprimées en millimètres et ne sont pas additionnées aux heures voisines dans ce produit.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'accumulated-precip': {
    what: 'Somme de la précipitation horaire depuis le début du RUN jusqu’à l’heure valide sélectionnée, maille par maille et en équivalent d’eau.',
    interpretation: [
      'Elle montre l’empreinte totale de l’épisode prévu par un même RUN et aide à localiser les maximums persistants ou orographiques. En avançant sur la ligne temporelle, elle ne devrait jamais diminuer dans une maille.',
      'Elle accumule aussi les erreurs d’intensité et de position de chaque heure. Comparer des cumuls de RUN différents exige d’indiquer clairement l’intervalle, car ils ne partagent pas nécessairement la même fenêtre temporelle.'
    ],
    method: 'Diagnostic MLX : somme de la précipitation de chaque heure (TOTAL_PRECIPITATION) entre H+01 et H+n. H+00 est fixé à zéro car il n’appartient pas à la période postérieure au début du RUN.',
    equations: [
      { label: 'Somme discrète sur la grille', latex: String.raw`P_{acum}(H+n,\,i,j)=\sum_{k=1}^{n}\max\!\left[P_{1h}(H+k,\,i,j),0\right]` }
    ],
    steps: [
      'Prendre la précipitation de chaque heure du même RUN.',
      'Ramener à zéro les petites valeurs négatives que peut apporter la donnée.',
      'Additionner maille par maille, sans interpoler dans l’espace ni entre les heures.',
      'Inclut la pluie et la neige en équivalent liquide du champ TOTAL_PRECIPITATION.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'relative-humidity-700': {
    what: 'Humidité relative de l’air sur la surface isobare de 700 hPa, exprimée par rapport à la saturation à la température de ce niveau même.',
    interpretation: [
      'Des bandes humides aident à localiser les nuages moyens, l’ascendance et l’alimentation des systèmes ; des intrusions sèches peuvent favoriser l’évaporation, les courants descendants ou l’érosion nuageuse quand elles coïncident avec de la précipitation.',
      'L’humidité relative dépend fortement de la température : une baisse n’implique pas nécessairement une perte de vapeur. Elle ne remplace pas l’eau précipitable ni ne décrit toute la colonne. Sur un relief avec p_s < 700 hPa, le niveau serait sous terre.'
    ],
    method: 'Humidité relative d’AROME (RELATIVE_HUMIDITY) à 700 hPa. MeteoLabX la convertit en pourcentage quand elle arrive en fraction 0–1 et limite le résultat à 0–100 %.',
    equations: [
      { label: 'Définition physique de référence', latex: String.raw`RH=100\,\frac{e}{e_s(T)}\ \%` },
      { label: 'Normalisation appliquée si AROME fournit une fraction', latex: String.raw`RH_{\%}=100\,RH_{0-1}` }
    ],
    steps: [
      'Variable d’AROME : RELATIVE_HUMIDITY sur les niveaux de pression.',
      'Niveau : 700 hPa.',
      'MeteoLabX ne recalcule ni e ni e_s : elle utilise le RH publié.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'shortwave-down': {
    what: 'Flux moyen horaire de rayonnement solaire de courte longueur d’onde qui atteint la surface vers le bas, somme de la composante directe et de la composante diffuse.',
    interpretation: [
      'Les maximums suivent l’ensoleillement, la hauteur solaire et les ciels dégagés ; des baisses locales signalent généralement de la nébulosité, du brouillard, des aérosols ou une ombre orographique représentée par le modèle. Utile pour l’énergie solaire et le bilan de surface.',
      'La valeur affichée est une moyenne horaire, pas une irradiance instantanée. Près du lever et du coucher du soleil, la moyenne peut différer beaucoup du maximum dans l’intervalle ; la nuit, elle doit s’approcher de zéro.'
    ],
    method: 'AROME publie le rayonnement de courte longueur d’onde descendant (DOWNWARD_SHORT_WAVE_RADIATION_FLUX) accumulé sur une heure. Bien que les métadonnées puissent annoncer des W/m², la donnée contient l’énergie reçue durant cette heure, en J/m² ; MeteoLabX divise par 3 600 s pour obtenir le flux moyen horaire.',
    equations: [
      { label: 'Conversion de l’énergie accumulée en flux moyen', latex: String.raw`\overline{F}_{SW\downarrow}=\frac{E_{SW\downarrow,\,PT1H}}{3600\ \mathrm s}` },
      { label: 'Décomposition physique', latex: String.raw`F_{SW\downarrow}=F_{dir}+F_{dif}` }
    ],
    steps: [
      'Variable d’AROME : DOWNWARD_SHORT_WAVE_RADIATION_FLUX accumulée sur une heure.',
      'Les valeurs négatives sont ramenées à zéro.',
      'Seule transformation MLX : multiplication par 1/3600.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'mu-ecape': {
    what: 'MU-ECAPE est la CAPE native d’AROME associée à la particule la plus instable des basses couches. Le produit du modèle inclut ses propres effets de dilution ou d’entraînement, c’est-à-dire le mélange d’air ambiant avec la particule pendant son ascension. MeteoLabX utilise le nom MU-ECAPE pour distinguer ce champ de sa MUCAPE conventionnelle, calculée sans entraînement.',
    interpretation: [
      'La carte recherche la partie de l’environnement à la plus grande flottabilité potentielle. C’est pourquoi elle peut montrer une instabilité élevée même si la surface est stable. Une valeur élevée indique que, même après la dilution représentée par AROME, il reste une quantité importante d’énergie disponible pour accélérer un courant ascendant.',
      'La comparaison la plus utile est avec MUCAPE MLX et avec ML-ECAPE. Si MU-ECAPE dépasse nettement ML-ECAPE, la couche la plus instable peut être surélevée ou peu représentative de la moyenne de la basse couche. Si MU-ECAPE est bien inférieure à MUCAPE MLX, le produit d’AROME représente une réduction importante de la flottabilité par dilution.'
    ],
    method: 'MeteoLabX affiche la variable CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY publiée par AROME, en J/kg. Elle ne reconstruit pas la particule, ne recalcule pas l’énergie et n’utilise pas SHARPpy.',
    equations: [
      { label: 'Forme physique générale d’une CAPE avec particule diluée', latex: String.raw`\mathrm{ECAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p}^{(entr)}-T_{v,e}}{T_{v,e}}\,dz` }
    ],
    steps: [
      'Origine de la donnée. La sélection de la particule MU, sa trajectoire et l’entraînement appartiennent au produit natif d’AROME.',
      'Traitement MeteoLabX. La valeur est affichée sans corrections ni combinaison avec le Lifted Index de MLX, qui est calculé sans entraînement.',
      'L’API publique ne documente pas la formulation exacte de la température virtuelle de la particule diluée, le taux d’entraînement, la fermeture ni la procédure précise de sélection de la particule. C’est pourquoi MU-ECAPE ne doit pas être comparée un à un avec MUCAPE MLX comme si seule une constante connue changeait.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'ml-ecape': {
    what: 'ML-ECAPE est la CAPE native d’AROME pour une particule représentative d’une basse couche mélangée, avec la dilution ou l’entraînement inclus par le produit du modèle lui-même. MeteoLabX l’étiquette ainsi pour la différencier de MLCAPE MLX, qui utilise une particule de couche mélangée conventionnelle sans entraînement.',
    interpretation: [
      'En représentant une moyenne de la basse couche, ML-ECAPE est généralement moins sensible qu’une particule de surface aux maximums très locaux de température ou d’humidité. Elle représente mieux l’instabilité moyenne disponible pour les orages qui s’alimentent de l’air de la couche limite.',
      'Elle doit se lire avec MU-ECAPE. Des valeurs proches suggèrent que la basse couche mélangée représente bien la particule la plus favorable ; une MU-ECAPE nettement supérieure peut signaler une couche surélevée plus instable ou une bande particulièrement chaude et humide que la moyenne ML lisse. La comparaison avec MLCAPE MLX aide à apprécier la réduction associée au produit avec entraînement.'
    ],
    method: 'MeteoLabX affiche la variable MEAN_LAYER_CAPE publiée par AROME, en J/kg, sans reconstruire la particule ni modifier l’énergie.',
    equations: [
      { label: 'Forme physique générale', latex: String.raw`\mathrm{ML\!\!-\!ECAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p,ML}^{(entr)}-T_{v,e}}{T_{v,e}}\,dz` }
    ],
    steps: [
      'Origine de la donnée. La sélection de la couche ML, la profondeur de mélange et l’entraînement sont internes au champ AROME.',
      'Traitement MeteoLabX. Le champ est représenté sans corrections postérieures.',
      'L’API publique ne documente pas la profondeur exacte de mélange ni le schéma d’entraînement, il ne faut donc pas supposer que ML-ECAPE utilise exactement les 100 hPa inférieurs employés par la MLCAPE conventionnelle de MeteoLabX.',
      'Le champ exprime une énergie potentielle ; pour évaluer si cette énergie peut se réaliser et quel type d’orage elle pourrait produire, il faut la combiner avec la CIN, le forçage, l’humidité, le cisaillement et la structure verticale.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'mucape-muli': {
    what: 'MUCAPE est la CAPE conventionnelle, sans entraînement, de la particule la plus instable des 300 hPa inférieurs du profil. MeteoLabX la représente en couleurs et dessine en isolignes le MULI, le Lifted Index calculé pour exactement la même particule MU.',
    interpretation: [
      'MUCAPE identifie la plus grande flottabilité potentielle, même si la particule d’origine est surélevée. MULI décrit la flottabilité de cette particule à 500 hPa : une valeur négative signifie que la particule arrive plus chaude que l’environnement. Une MUCAPE élevée et un MULI très négatif renforcent le signal d’instabilité, mais ne garantissent ni qu’il existe un déclenchement ni que la particule puisse vaincre l’inhibition.',
      'Comme l’ascension n’inclut ni entraînement, ni charge d’eau, ni mélange latéral, MUCAPE fonctionne comme une limite supérieure idéalisée de l’énergie du courant ascendant. Il convient de la comparer à MU-ECAPE pour apprécier la réduction représentée par AROME, et à la CIN pour évaluer si la particule peut atteindre le niveau de convection libre (NCL).'
    ],
    method: 'MeteoLabX applique son propre calcul de particule. Elle sélectionne le maximum de température potentielle équivalente de Bolton entre la surface et 300 hPa au-dessus, élève la particule à sec jusqu’au NCS puis pseudoadiabatiquement ensuite. La flottabilité utilise la température virtuelle et l’énergie s’intègre par trapèzes. Elle n’appelle pas params.cape de SHARPpy.',
    equations: [
      { label: 'Sélection MU', latex: String.raw`p_{MU}=\operatorname*{arg\,max}_{p_s-300\le p\le p_s}\theta_e(p)` },
      { label: 'Énergie positive', latex: String.raw`\mathrm{MUCAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p}-T_{v,e}}{T_{v,e}}\,dz` },
      { label: 'Lifted Index de la même particule', latex: String.raw`\mathrm{MULI}=T_e(500\,hPa)-T_{p,MU}(500\,hPa)` }
    ],
    steps: [
      'Sélectionner la particule MU. On recherche le maximum de θe dans les 300 hPa inférieurs.',
      'Élever la particule. L’ascension est sèche jusqu’au NCS et pseudoadiabatique au-dessus, avec le condensat éliminé et sans entraînement.',
      'Intégrer la CAPE entre le premier NCL et le dernier niveau d’équilibre. Le point exact où la particule commence ou cesse de flotter est calculé entre les deux niveaux les plus proches, et si, en chemin, il y a des tronçons où elle redevient plus froide que l’air environnant, cette énergie négative est soustraite. Si la particule flotte encore au niveau le plus haut disponible, la somme s’arrête là et le niveau d’équilibre reste sans valeur, car il se situe au-dessus des données. Les hauteurs proviennent d’une intégration hypsométrique.',
      'Calculer MULI. On soustrait la température de la particule MU à celle de l’environnement à 500 hPa.'
    ],
    sources: [NOAA_CAPE, NOAA_LI, SHARPPY]
  },

  'mlcape-mlli': {
    what: 'MLCAPE est la CAPE sans entraînement d’une particule représentative des 100 hPa inférieurs. MeteoLabX mélange cette couche au moyen de la température potentielle et du rapport de mélange moyens. Les couleurs montrent MLCAPE et les isolignes montrent le MLLI de la même particule.',
    interpretation: [
      'MLCAPE lisse les pics très locaux de température ou d’humidité et représente généralement mieux une couche limite bien mélangée. Elle convient à une convection qui ingère une épaisseur d’air, pas seulement les conditions du premier niveau proche de 2 m.',
      'Un MLLI négatif renforce le signal d’instabilité à 500 hPa. Une SBCAPE nettement supérieure à MLCAPE peut révéler une couche de surface chaude ou humide extrêmement fine ; une MUCAPE nettement supérieure à MLCAPE peut indiquer que la couche la plus instable est surélevée.'
    ],
    method: 'MeteoLabX moyenne θ et le rapport de mélange r sur les 100 hPa inférieurs, reconstruit la température et le point de rosée à la pression de surface et élève la particule avec le même schéma pseudoadiabatique, en température virtuelle et sans entraînement utilisé pour MU.',
    equations: [
      { label: 'Propriétés de la particule ML100', latex: String.raw`\bar\theta=\frac{1}{\Delta p}\int_{p_s-100}^{p_s}\theta\,dp,\qquad \bar r=\frac{1}{\Delta p}\int_{p_s-100}^{p_s}r\,dp` },
      { label: 'Énergie et LI', latex: String.raw`\mathrm{MLCAPE}=\int_{LFC}^{EL}B\,dz,\qquad \mathrm{MLLI}=T_e(500)-T_{p,ML}(500)` }
    ],
    steps: [
      'Définir la couche ML100. On prend les 100 hPa situés immédiatement au-dessus de la pression de surface.',
      'Mélanger ses propriétés. On moyenne θ et r et on reconstruit T et Td de la particule à p_s.',
      'Élever et intégrer. La particule monte à sec jusqu’au NCS et pseudoadiabatiquement jusqu’au EL ; la CAPE utilise la flottabilité virtuelle.',
      'Calculer MLLI. Les isolignes utilisent la température à 500 hPa de exactement la même particule ML.'
    ],
    sources: [NOAA_CAPE, NOAA_LI, SHARPPY]
  },

  'sbcape-sbli': {
    what: 'SBCAPE est la CAPE sans entraînement d’une particule qui part des conditions de surface du profil, construites avec une température et un point de rosée proches de 2 m. Les couleurs montrent SBCAPE et les isolignes le SBLI de cette même particule.',
    interpretation: [
      'Elle est particulièrement sensible au cycle diurne, aux brises, aux fronts de rafales et aux plages froides. Utile pour une convection clairement enracinée en surface, bien qu’elle puisse exagérer une couche chaude ou humide trop fine.',
      'Une SBCAPE élevée avec un SBLI négatif indique une flottabilité potentielle d’une particule de surface, mais n’assure pas qu’elle vainque l’inhibition. Si SBCAPE diminue tandis que MUCAPE reste élevée, l’instabilité peut s’être surélevée au-dessus d’une couche de surface stable.'
    ],
    method: 'MeteoLabX insère T et Td de surface comme premier niveau et élève cette particule avec le même schéma pseudoadiabatique, en température virtuelle et sans entraînement utilisé pour les autres CAPE MLX.',
    equations: [
      { label: 'Énergie de surface', latex: String.raw`\mathrm{SBCAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p,SFC}-T_{v,e}}{T_{v,e}}\,dz` },
      { label: 'Indice de surface', latex: String.raw`\mathrm{SBLI}=T_e(500\,hPa)-T_{p,SFC}(500\,hPa)` }
    ],
    steps: [
      'Fixer l’origine. On utilise la pression de surface et T/Td du premier niveau.',
      'Élever la particule. L’ascension est sèche jusqu’au NCS et pseudoadiabatique au-dessus.',
      'Calculer l’énergie et l’indice. La CAPE s’intègre en hauteur et le LI s’évalue à 500 hPa.',
      'Représenter le résultat. SBCAPE est montrée en couleurs et SBLI en isolignes.'
    ],
    sources: [NOAA_CAPE, NOAA_LI]
  },

  dcape: {
    what: 'La DCAPE mesure l’énergie qui pourrait accélérer vers le bas une particule d’air. On peut la comprendre comme l’équivalent descendant de la CAPE : tandis que la CAPE additionne la flottabilité positive qui propulse un courant ascendant, la DCAPE additionne la flottabilité négative qui peut propulser un courant descendant. Quand la pluie, la grêle ou la neige tombent à travers de l’air non saturé, une partie de l’eau s’évapore ou se sublime. Ces changements de phase consomment de la chaleur et refroidissent l’air qui entoure les hydrométéores. L’air refroidi devient plus dense que l’environnement, et cette différence de densité produit une force descendante. La particule se réchauffe par compression en descendant, mais dans une basse couche à fort gradient thermique, l’environnement peut se réchauffer vers le sol encore plus vite. Quand le noyau descendant atteint le sol, il s’étale horizontalement, forme le front de rafales et peut produire une rafale descendante.',
    interpretation: [
      'Une DCAPE élevée apparaît généralement en présence d’air relativement sec dans les basses ou moyennes couches et d’une baisse marquée de la température avec l’altitude. Cette combinaison permet beaucoup de refroidissement par évaporation et maintient la particule froide pendant la descente, ce qui signale des environnements favorables aux rafales descendantes.',
      'Comme échelle idéalisée, si toute l’énergie se transformait en vitesse verticale, le courant descendant pourrait s’approcher par w ≈ √(2 · DCAPE).',
      'Cette relation ne calcule pas la rafale en surface. Une partie de l’énergie se perd par mélange, frottement et évaporation incomplète, et le vent observé dépend aussi de la quantité de moment que l’orage transporte depuis les niveaux élevés et de la manière dont le flux s’organise en atteignant le sol.',
      'Une DCAPE faible n’exclut pas non plus un vent dommageable.'
    ],
    method: 'MeteoLabX reproduit la procédure SPC/SHARPpy et cherche une particule particulièrement favorable au refroidissement dans les 400 hPa inférieurs du profil. L’implémentation utilise la température ordinaire, comme params.dcape de SHARPpy, et une intégration trapézoïdale. Si SHARPpy n’est pas disponible, MeteoLabX obtient la température de la particule en inversant la θe saturée.',
    equations: [
      { label: 'Sélection de la couche d’origine', latex: String.raw`p_0=p_{base,\,\min\overline{\theta_e}}-50\,hPa` },
      { label: 'Intégration de la flottabilité négative', latex: String.raw`\mathrm{DCAPE}=-R_d\int_{p_s}^{p_0}\left(T_e-T_p\right)d\ln p` },
      { label: 'Échelle idéalisée de vitesse verticale', latex: String.raw`w_{ideal}\approx\sqrt{2\,\mathrm{DCAPE}}` }
    ],
    steps: [
      'Chercher l’air d’origine. On teste des couches mobiles de 100 hPa et on choisit celle qui a la plus faible température potentielle équivalente moyenne.',
      'Situer la particule. La particule part du centre de cette couche de 100 hPa.',
      'Représenter le refroidissement initial. Sa température est amenée au thermomètre mouillé, comme approximation du refroidissement produit par l’évaporation de la précipitation jusqu’à saturation.',
      'La faire descendre. La particule descend pseudoadiabatiquement jusqu’à la surface et se compare à chaque niveau à la température ambiante.',
      'Additionner la flottabilité négative. La différence thermique s’intègre tout au long de la descente, avec la température ordinaire et sans correction de température virtuelle ; une particule plus froide que l’environnement et une couche plus profonde produisent une DCAPE plus élevée.'
    ],
    sources: [SHARPPY]
  },

  'ordinary-cell-motion': {
    what: 'C’est une estimation de la composante advective du mouvement d’une cellule convective ordinaire. Elle se calcule avec le vent moyen pondéré par la pression à l’intérieur du nuage d’une particule ML100, entre son NCS et son EL.',
    interpretation: [
      'Les couleurs montrent la vitesse estimée et les lignes de courant la direction de translation par le flux moyen. Utile pour anticiper vers où se déplacerait une cellule non supercellulaire et combien de temps elle pourrait rester au-dessus d’une zone.',
      'Elle n’inclut pas la propagation par de nouveaux développements, les plages froides, la scission des supercellules, l’interaction avec des frontières ni l’ancrage orographique.'
    ],
    method: 'MeteoLabX calcule d’abord le NCS et le EL de la particule ML100. Elle intègre ensuite U et V par trapèzes sur la pression, découpe chaque couche isobare à l’intervalle nuageux et divise par la profondeur de pression.',
    equations: [
      { label: 'Vecteur de mouvement ordinaire', latex: String.raw`\vec C_{cel}=\frac{1}{p_{LCL}-p_{EL}}\int_{p_{EL}}^{p_{LCL}}\vec V(p)\,dp` },
      { label: 'Vitesse montrée en couleurs', latex: String.raw`C_{cel}=\sqrt{C_u^2+C_v^2}` }
    ],
    steps: [
      'Définir le nuage. Le NCS et le EL proviennent de la particule ML100 calculée par MeteoLabX.',
      'Construire le profil de vent. U/V proviennent de la surface et des niveaux isobares d’AROME.',
      'Moyenner par pression. On calcule le chevauchement exact de chaque couche avec l’intervalle NCS–EL et on intègre linéairement par trapèzes.',
      'Représenter le vecteur. La magnitude apparaît en couleurs et la direction par des lignes de courant.',
      'Ce n’est ni Bunkers ni Corfidi et cela ne représente pas des supercellules ou des systèmes. Cela décrit uniquement l’advection par le vent moyen à l’intérieur du nuage.'
    ],
    sources: [CELL_MOTION, NOAA_CAPE]
  },

  ship: {
    what: 'SHIP résume dans quelle mesure l’environnement pourrait permettre à un orage de produire de la grêle très grosse. Il ne vise pas à détecter n’importe quel épisode de grêle : il a été conçu pour reconnaître les environnements associés à de la grêle significative, environ 5 cm ou plus dans son contexte d’origine aux États-Unis. L’indice combine plusieurs conditions qui doivent coïncider : un courant ascendant capable de soutenir les grêlons, une humidité apportant de l’eau en surfusion, une zone froide où la grêle peut croître et suffisamment de cisaillement pour que l’orage reste organisé. Si l’un de ces ingrédients est clairement défavorable, le résultat diminue.',
    interpretation: [
      'MUCAPE représente l’énergie disponible pour accélérer le courant ascendant. Un courant intense peut maintenir la grêle en suspension plus longtemps et lui permettre de continuer à croître.',
      'L’humidité de la particule MU favorise le fait que l’orage produise abondamment de l’eau liquide en surfusion. Le gradient 700–500 hPa et la température à 500 hPa décrivent une couche moyenne froide avec une forte baisse thermique. Le cisaillement 0–6 km aide à séparer le courant ascendant de la précipitation, et la hauteur du 0 °C situe les zones de congélation et de fusion.',
      'Une valeur élevée signifie que plusieurs ingrédients favorables coïncident au même endroit et au même moment. Si un orage se développe en exploitant cette particule et parvient à s’organiser, l’environnement permet une croissance efficace de la grêle.',
      'SHIP ne représente ni la taille prévue des grêlons, ni la quantité de grêle, ni la probabilité qu’il grêle en un point donné. Une valeur faible n’exclut pas non plus de la grêle sévère.',
      'Les seuils proviennent du contexte opérationnel du SPC des États-Unis et ne sont pas calibrés pour l’Europe. Il convient de les utiliser comme un repère relatif, pas comme des catégories universelles de risque.'
    ],
    method: 'MeteoLabX obtient SHIP par la fonction sharppy.sharptab.params.ship de SHARPpy. Pour chaque point de la carte, elle prépare les ingrédients de la particule la plus instable et du profil ambiant, et applique la formulation opérationnelle.',
    equations: [
      { label: 'Cœur de la formulation SHARPpy/SPC', latex: String.raw`\mathrm{SHIP}_0=-\frac{\mathrm{MUCAPE}\;r_{MU}\;\Gamma_{700-500}\;T_{500}\;\mathrm{BWD}_{0-6}}{42\,000\,000}` },
      { label: 'Facteurs réducteurs', latex: String.raw`\mathrm{SHIP}=\max(0,\mathrm{SHIP}_0)\,f_C\,f_\Gamma\,f_F` },
      { label: 'Définition des réducteurs', latex: String.raw`f_C=\min\!\left(1,\frac{\mathrm{MUCAPE}}{1300}\right),\quad f_\Gamma=\min\!\left(1,\frac{\Gamma_{700-500}}{5.8}\right),\quad f_F=\min\!\left(1,\frac{z_{0^\circ C}}{2400}\right)` }
    ],
    steps: [
      'Préparer la particule MU. On prend la MUCAPE MLX et le rapport de mélange de cette même particule la plus instable, afin que l’énergie et l’humidité décrivent le même air d’origine.',
      'Construire les champs verticaux. À partir du profil AROME, on calcule le gradient thermique 700–500 hPa, la température à 500 hPa, la BWD géométrique entre la surface et 6 km, et la hauteur AGL du niveau de 0 °C.',
      'Évaluer SHIP avec SHARPpy. MeteoLabX transmet ces ingrédients à sharppy.sharptab.params.ship, qui les combine maille par maille selon la formulation SPC.',
      'Appliquer les limites opérationnelles. La fonction restreint l’humidité MU à l’intervalle 11–13,6 g/kg et la BWD 0–6 km à 7–27 m/s, et applique à T500 la limite de −5,5 °C.',
      'Réduire et clore le résultat. Une MUCAPE inférieure à 1300 J/kg, un gradient 700–500 hPa inférieur à 5,8 °C/km ou un niveau de 0 °C sous 2400 m AGL réduisent progressivement SHIP. La valeur finale est sans dimension et tronquée à zéro. SHIP n’utilise pas l’EBWD dans cette formulation.'
    ],
    sources: [SHARPPY]
  },

  'cloud-cover': {
    what: 'Fraction totale de la maille couverte par des nuages à n’importe quel niveau de la colonne atmosphérique, exprimée en pourcentage.',
    interpretation: [
      'Des valeurs élevées indiquent un ciel largement couvert et réduisent généralement le rayonnement solaire ; les gradients marquent les bords des systèmes nuageux. Une valeur de 100 % ne renseigne ni sur l’épaisseur, ni sur la base, ni sur le sommet, ni sur la phase, ni sur la précipitation.',
      'La nébulosité totale peut être dominée par une couche haute fine ou par des stratus bas denses, des situations météorologiquement distinctes. À combiner avec les niveaux de nébulosité, l’humidité, la précipitation et le rayonnement.'
    ],
    method: 'Nébulosité totale d’AROME (TOTAL_CLOUD_COVER). MeteoLabX utilise la valeur publiée ; si elle arrive en fraction 0–1, elle la multiplie par 100 et limite le résultat à l’intervalle physique 0–100 %.',
    equations: [
      { label: 'Normalisation d’unité quand nécessaire', latex: String.raw`C_{total}[\%]=100\,C_{total}[0,1]` }
    ],
    steps: [
      'Variable d’AROME : TOTAL_CLOUD_COVER.',
      'Elle n’est pas reconstruite en additionnant nuages bas, moyens et hauts.',
      'La règle interne de chevauchement des couches appartient au modèle et ne se déduit pas de l’API.'
    ],
    sources: [MF_AROME, MF_API]
  }
};

export default forecastProductGuides;
