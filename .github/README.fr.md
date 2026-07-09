<div align="center">

<img src="../assets/doll/idle.png" width="112" alt="Icône Wisp" />

# Wisp

**De nombreuses tâches sont mieux traitées avec l'assistance de l'IA qu'avec une délégation complète. Wisp rend cette collaboration plus rapide, plus conviviale et plus personnalisable en tant que plateforme ouverte de co-travail.**

Wisp vous offre une IA pilotée par raccourcis clavier qui peut lire votre sélection, presse-papiers, application, navigateur, documents ou capture d'écran pendant que vous restez là où vous êtes. Appuyez sur un raccourci, choisissez une action, et diffusez la réponse dans une petite superposition ou au niveau de votre curseur de saisie. Il est entièrement open-source, multiplateforme, extensible, sous licence permissive et 100% Python, afin de rester facile à modifier: le type d'ouverture que même des produits valant des milliards comme Microsoft Copilot n'offrent toujours pas.

[![Plateforme](https://img.shields.io/badge/plateforme-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#état-des-plateformes)
[![Python](https://img.shields.io/badge/python-3.12-3572A5?style=flat-square)](#démarrage-rapide)
[![Local d'abord](https://img.shields.io/badge/local--first-contexte%20et%20mémoire-4B8F8C?style=flat-square)](#confidentialité-et-contrôle)
[![Licence](https://img.shields.io/badge/licence-MIT-7C3AED?style=flat-square)](#licence)

**Langues :** [English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | Français | [Español](README.es.md)

**Site web :** [Documentation Wisp](https://sunnylich.github.io/Wisp-AI-Assistant/)

[Démarrage rapide](#démarrage-rapide) | [Fonctionnalités](#ce-que-fait-wisp) | [Démos](#démos) | [Configuration](#configuration) | [APIs gratuites](#sources-dapi-de-modèles-gratuites) | [Confidentialité](#confidentialité-et-contrôle)

![Démo Wisp Ctrl+Q](readme-assets/readme-1st-demo.gif)

**Requête en superposition :** Appuyez sur un raccourci, choisissez une action, et obtenez une réponse diffusée sans quitter l'application que vous utilisez déjà.
</div>

---

## Problèmes connus

[Problèmes connus](https://sunnylich.github.io/Wisp-AI-Assistant/#known-issues)

## Ce que fait Wisp

Wisp est conçu pour les moments où ouvrir une application de chat briserait votre flux de travail.

Sélectionnez du texte, appuyez sur le raccourci général, appuyez sur une touche d'action, et Wisp interroge votre modèle configuré avec uniquement les sources de contexte que vous avez activées. Les réponses s'affichent en flux dans une bulle compacte à côté de l'icône flottante. Si le TTS est activé, la réponse est prononcée à mesure qu'elle arrive.

| Au lieu de... | Wisp vous permet de... |
| --- | --- |
| Copier du texte dans une fenêtre de chat séparée | Demander depuis l'application que vous utilisez déjà |
| Retaper les instructions pour des tâches récurrentes à chaque fois | Enregistrer des actions réutilisables avec les sources de contexte voulues |
| Transformer chaque pensée en invite tapée | Maintenir un raccourci vocal, parler et envoyer la demande transcrite |
| Vous épuiser à lire des murs de texte | Diffuser les réponses dans la superposition ou les écouter avec le TTS |
| Expliquer manuellement ce qui est à l'écran | Capturer la sélection, le presse-papiers, des documents, des pages de navigateur et des captures d'écran |
| Confier vos invites, votre contexte et votre mémoire à une plateforme d'assistant fermée | Garder les données sur votre machine et envoyer uniquement les informations et requêtes que vous choisissez à votre fournisseur de modèle |

## Points forts

- **Superposition d'abord** — une icône flottante, un sélecteur d'action et une bulle de réponse restent au premier plan sans envahir votre bureau.
- **Confidentialité par défaut** — Wisp n'a pas de couche de stockage hébergée ; les données restent sur votre machine, et le mode confidentialité peut avertir ou masquer avant que le contexte sensible ne parte.
- **Hautement personnalisable** — chaque raccourci, touche d'action, invite, source de contexte, comportement de collage, route de modèle, paramètre vocal et dimension de bulle peut être modifié.
- **Interface graphique accessible** — les paramètres, les vérifications de configuration, les rapports de confidentialité, les outils de mémoire et les avertissements de modèle expliquent ce qui se passe sans nécessiter de lire le code.
- **Capture de contexte** — Wisp peut lire le texte sélectionné, le texte du presse-papiers, l'interface utilisateur ciblée, les documents ouverts, le contenu du navigateur, les fichiers récents et des captures d'écran optionnelles.
- **Voix entrée et sortie** — STT local via faster-whisper, plus TTS neuronal local sur l'appareil (Kokoro et clonage vocal GPT-SoVITS) ou voix cloud/compatibles (Cartesia, ElevenLabs, OpenAI, tout serveur compatible OpenAI), avec TTS désactivé par défaut.
- **Captures visuelles** — tracez une région avec `Ctrl+Alt+Q` et envoyez la capture d'écran à un modèle de vision.
- **Réécriture et collage** — utilisez le raccourci de réécriture pour réécrire le texte sélectionné avec le contexte capturé et coller le résultat dans le champ actif.
- **Apportez votre propre fournisseur** — Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, XAI, Together, Cerebras, serveurs compatibles OpenAI personnalisés, GitHub Copilot, et plus.
- **Mémoire locale** — une mémoire à court et long terme optionnelle est stockée localement, avec un visualiseur pour modifier ou supprimer des faits.
- **Extensions** — étendez Wisp avec des hooks, des actions de barre d'état, des paramètres, des outils appelables par le modèle, des actions configurables et des raccourcis.
- **Tâches d'agent** — un cadre de tâches en bac à sable existe pour les travaux plus longs nécessitant décomposition, révision et artefacts.

## Démos

![Démo de capture d'écran Wisp Ctrl+Alt+Q](readme-assets/readme-2nd-demo.gif)

**Capture visuelle :** Le flux de capture est destiné aux cas où le contexte visuel est important. `Ctrl+Alt+Q` vous permet de tracer une région, d'envoyer uniquement ce recadrage à un modèle de vision, et de garder la réponse dans la superposition au lieu de changer d'application.

![Démo de réécriture contextuelle Wisp](readme-assets/readme-3rd-demo.gif)

**Réécriture contextuelle :** Wisp peut rassembler un contexte d'application utile sans prendre de capture d'écran, donc le modèle sait sur quoi vous travaillez. Puis le raccourci de réécriture réécrit uniquement le texte sélectionné et vise le collage dans le champ d'origine capturé au moment du raccourci.

![Démo de tâche multi-agent Wisp](readme-assets/readme-4th-demo.gif)

**Exécution d'agent en bac à sable :** Le flux de tâche d'agent est conçu pour les travaux d'espace de travail plus longs. Wisp peut répartir une tâche entre les rôles de coordinateur, constructeur et réviseur, inspecter les fichiers de projet, effectuer un changement ciblé, exécuter des vérifications, et laisser un rapport final et des artefacts pour l'exécution.

## Flux de travail

| De votre côté | Ce que fait Wisp |
| --- | --- |
| Sélectionnez du texte, choisissez du contexte ou tracez une capture | Capture uniquement le contexte sélectionné ou activé |
| Appuyez sur le raccourci d'appel et choisissez une action ou une invite personnalisée | Construit la requête du modèle à partir de votre invite et du contexte choisi |
| Envoyez la requête | L'envoie directement à votre fournisseur de modèle configuré |
| Attendez la réponse | Diffuse la réponse dans une bulle, avec TTS automatique optionnel |
| Gardez des informations utiles pour plus tard | Stocke la mémoire localement seulement lorsque la mémoire est activée |

Exemples de flux :

| Ce que vous voulez | Ce que fait Wisp |
| --- | --- |
| Vous voulez une explication du texte sélectionné | Lit la sélection après que vous appuyez sur le raccourci général et choisissez `W` (Qu'est-ce que c'est ?) ou `A` (Expliquer simplement), puis l'explique dans la superposition |
| Vous voulez réécrire une phrase | Lit la phrase sélectionnée, applique l'action de réécriture choisie et peut recoller le résultat |
| Vous devez poser votre propre question | Envoie votre invite personnalisée avec le contexte activé pour cet appelant |
| Un élément d'interface ou une image est déroutant | Envoie la capture `Ctrl+Alt+Q` à un modèle de vision |
| Vous voulez interroger le modèle par voix | Transcrit votre demande vocale `F9` et l'envoie comme requête au modèle |
| Vous voulez dicter dans une autre application | Transcrit votre voix `F8` directement dans le champ de texte ciblé |
## Démarrage rapide

Il y a deux façons supportées de démarrer Wisp.

### Option 1 : Application packagée

Utilisez ceci si vous voulez l'application sans cloner le dépôt ou gérer les dépendances Python.

1. Téléchargez le dernier artefact pour votre plateforme depuis [GitHub Releases](https://github.com/SunnyLich/Python-AI-assistant-overlay/releases).
2. Décompressez l'archive et démarrez l'application packagée.
3. Ouvrez les Paramètres pour ajouter vos clés de fournisseur de modèle, paramètres vocaux et raccourcis préférés.

| OS | Artefact de version | Démarrer avec |
| --- | --- | --- |
| Windows | `Wisp-<tag>-windows-x64.zip` | `Wisp.exe` |
| macOS | `Wisp-<tag>-macos-<arch>.zip` | `Wisp.app` |
| Linux | `Wisp-<tag>-linux-x64.tar.gz` | `Wisp` |

### Option 2 : Lanceur de dépôt

Utilisez ceci si vous voulez exécuter depuis la source, développer Wisp, ou tester la dernière extraction.

Clonez le dépôt :

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

Puis démarrez Wisp avec le lanceur de dépôt pour votre plateforme :

| OS | Démarrer avec | Source des dépendances |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements/requirements-windows.lock` |
| macOS | `Start Wisp.command` | `requirements/requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements/requirements-linux.lock` |

Le premier lancement provisionne l'environnement Python et installe les dépendances. Les lancements ultérieurs vont directement dans l'application.

Pour construire votre propre copie packagée, voir [Construire un EXE](../docs/BUILDING_EXE.md) pour les commandes de construction locale et le flux de travail de version taguée.

Prérequis :

- Python `3.12`, épinglé dans `.python-version`
- Windows 10/11, macOS 13+, ou Linux avec X11 pour le chemin complet raccourcis/capture d'écran
- Au moins une clé de fournisseur LLM configurée ou un serveur compatible local

Pour les journaux d'exécution complets, utilisez le lanceur de débogage correspondant :

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## Configuration

Utilisez la fenêtre Paramètres pour la configuration normale. Elle peut stocker les clés de fournisseur, choisir les routes de modèle, configurer la voix, exécuter une vérification de configuration, expliquer les fonctionnalités optionnelles manquantes, et afficher des avertissements pour les capacités de modèle non supportées. Les clés fournisseur et les tokens OAuth sont enregistrés dans le **trousseau du système** : Gestionnaire d'identifiants Windows, Trousseau macOS ou Secret Service/KWallet sous Linux, **pas dans un fichier de configuration en clair**.

Pour les builds de source et les configurations avancées, `.env.example` documente les clés de configuration disponibles. Vous n'avez généralement pas besoin de les modifier manuellement.

Pour les options de modèles sans coût et avec offres gratuites, voir [Sources d'API de modèles gratuites](#sources-dapi-de-modèles-gratuites).

## Raccourcis par défaut

| Raccourci | Action |
| --- | --- |
| `Ctrl+Q` sous Windows, `Ctrl+Alt+Space` sous macOS/Linux | Ouvrir le sélecteur d'action général |
| `Ctrl+Shift+Q` sous Windows, `Ctrl+Alt+Shift+Space` sous macOS/Linux | Ouvrir le sélecteur d'action de réécriture/collage |
| `Ctrl+Alt+Q` | Tracer une capture d'écran pour la vision |
| `Alt+Q` | Ajouter la sélection actuelle au tampon de contexte |
| `Alt+W` | Vider le tampon de contexte |
| Maintenir `F9` | Enregistrer la voix, transcrire et interroger |
| Maintenir `F8` | Dictée directe dans le champ de texte ciblé |
| `F7` | Lire le texte sélectionné à voix haute |
| `W` / `A` / `D` | Déclencher les lignes d'action intégrées |
| `S` | Mode d'invite personnalisée |
| `Échap` | Annuler le sélecteur |

Chaque appelant, raccourci, étiquette, invite, source de contexte, paramètre de recoller et dimension d'interface est configurable depuis les Paramètres.

## Extensions

Profondément extensible, Wisp se transforme avec les extensions : nouvelles fonctionnalités, nouveaux flux de travail, nouvelles possibilités. Chaque extension vit dans son propre dossier sous `addons/` avec un manifeste `addon.toml`, et s'exécute dans son propre **processus hôte Python isolé**, donc un crash, un hook lent, ou une mauvaise dépendance dans une extension ne peut pas faire tomber le worker cerveau ou toute autre extension. **Les capacités sont opt-in :** une extension ne reçoit que ce que son manifeste déclare, et les permissions manquantes sont refusées. Les extensions qui ont besoin de packages tiers obtiennent un environnement virtuel dédié que vous approuvez avant qu'il s'exécute.

Une extension peut s'accrocher à Wisp à plusieurs points :

- **Contexte** — lire ou réécrire l'invite et le contexte avant l'envoi d'une requête.
- **Outils** — enregistrer des outils appelables par le modèle que le modèle peut invoquer en cours de réponse.
- **Réponses** — observer les réponses complétées pour les journaliser, les sauvegarder ou les transmettre.
- **Actions et raccourcis** — ajouter ses propres lignes d'action et raccourcis globaux avec des invites personnalisées.
- **Interface** — contribuer des actions de barre d'état, des champs de paramètres et des notifications.
- **Actions LLM** — exécuter ses propres appels de modèle limités depuis un hook ou un raccourci.

**Ce que les extensions peuvent faire :** parce qu'une extension peut injecter du contexte, exposer des outils et réagir aux réponses, la surface est large. Quelques exemples, et le hook que chacun utilise :

| Vous voulez... | Hook | Le manifeste a besoin de |
| --- | --- | --- |
| Tirer votre git diff, calendrier, ou un ticket ouvert dans l'invite automatiquement | Contexte (`before_query`) | `query = "modify"` |
| Donner au modèle un outil pour rechercher un wiki interne, interroger une base de données, appeler une API météo ou boursière, ou basculer un appareil domotique | Outils (`get_tools`) | `tools = true` (plus `[dependencies]` pour les packages) |
| Masquer ou taguer le contexte sensible sortant pour la conformité | Contexte (`before_query`) | `query = "modify"` |
| Ajouter chaque réponse à un journal quotidien, ou la pousser vers Notion ou Slack | Réponses (`after_response`) | `response = "read"` |
| Ajouter une action "réécrire dans notre style maison" à une touche soutenue par sa propre invite | Actions et raccourcis | `[[intents]]` / `[[hotkeys]]`, `hotkeys = true` |

Si vous pouvez l'écrire en Python et qu'il s'adapte à l'un des points de hook ci-dessus, vous pouvez le connecter à la même superposition pilotée par raccourcis que vous utilisez déjà.

## Client et serveur MCP

### Client MCP : utiliser des serveurs externes dans Wisp

Wisp est fourni avec une extension **pont MCP** (`addons/mcp_bridge`) qui agit comme client MCP : indiquez n'importe quels serveurs [Model Context Protocol](https://modelcontextprotocol.io) dans son `servers.json` et Wisp expose toute leur boîte à outils à son modèle en tant qu'outils Wisp. La superposition peut ainsi utiliser des capacités MCP externes sans quitter le flux de travail du bureau. Voir le [Guide des extensions](../addons/README.md) pour le contrat complet de manifeste et de hook, ou la page **Extensions** sur le [site de documentation Wisp](../Wisp%20Website/Wisp%20Docs.html).

### Serveur MCP : serveur de contexte Wisp

Wisp inclut également un **serveur MCP stdio local** nommé **Wisp Context Server**. Des clients MCP de confiance tels que Claude Desktop, Cursor et Codex peuvent le lancer pour lire le contexte actif du bureau ; l'application Wisp n'a pas besoin de rester ouverte.

Il fournit cinq outils en lecture seule :

- `get_selected_text` : le texte actuellement sélectionné sur le bureau.
- `get_clipboard` : le texte du presse-papiers.
- `get_active_window` : l'application active, le titre de la fenêtre et, lorsqu'elle est disponible, l'URL du navigateur.
- `read_browser_page` : le texte de la page visible dans le navigateur.
- `take_screen_snip` : une capture de l'écran principal.

### Connecter un client

Démarrez Wisp une fois, puis copiez l'entrée `mcpServers` de `addons/mcp_bridge/claude_config_snippet.json` dans la configuration de votre client MCP. Wisp génère cet extrait avec le chemin local correct vers son propre interpréteur Python et `addons/mcp_bridge/context_server.py` ; ne le remplacez pas par le Python du système. Consultez le [guide de configuration du serveur MCP Bridge](../addons/mcp_bridge/README.md) pour les notes propres à chaque plateforme et le dépannage.

N'enregistrez le serveur qu'auprès de clients de confiance : les résultats des outils peuvent contenir du texte sélectionné, le contenu du presse-papiers, le contenu du navigateur et des captures de votre bureau.

## Confidentialité et contrôle

Wisp est conçu comme un assistant de bureau local. **Le stockage reste sur votre machine**, et les requêtes vont directement au fournisseur de modèle ou au serveur local que vous configurez.

- **Les données locales restent locales :** les paramètres, les chats, la mémoire, les rapports de confidentialité et la configuration sont stockés sur votre machine.
- **Clés dans le trousseau du système :** les clés fournisseur et les tokens OAuth sont stockés dans le stockage sécurisé de mots de passe intégré à Windows, macOS ou votre bureau Linux.
- **Requêtes directes :** les requêtes de modèle vont directement de votre machine au fournisseur ou serveur local que vous avez configuré.
- **Vous choisissez ce qui est envoyé :** votre fournisseur de modèle configuré reçoit uniquement l'invite que vous envoyez et les sources de contexte sélectionnées ou activées pour cet appelant.
- **Les aperçus restent locaux :** Wisp peut inspecter le contexte disponible localement pour afficher des estimations de tokens, la disponibilité et les comptes de suppression de confidentialité avant que vous envoyiez. Prévisualiser une source ne l'envoie pas au fournisseur de modèle ni ne la sauvegarde en tant que chat/mémoire.
- **Contexte contrôlé par raccourci :** le contexte d'application ambiant, le presse-papiers, les documents, les pages de navigateur, le contexte GitHub, la mémoire, les outils et les captures d'écran peuvent chacun être activés, désactivés ou routés à la demande.
- **Mode confidentialité :** les vérifications de configuration prioritaires à la confidentialité et le comportement d'avertissement restent activés, y compris l'état de suppression avant l'envoi de contexte sensible.
- **Inactif tant que non configuré :** la voix optionnelle, la lecture de documents, le contenu du navigateur, les captures d'écran, GitHub Copilot et les extensions restent inactifs jusqu'à leur configuration.
- **Pas de connexions surprises :** le TTS cloud, les fournisseurs de modèle, les serveurs compatibles, ou GitHub Copilot ne sont contactés que lorsque vous configurez et utilisez ces fonctionnalités.
- **Extensions isolées :** les extensions s'exécutent dans des processus hôtes Python isolés et doivent déclarer les capacités dont elles ont besoin.
- **Vérifications de configuration légères :** les piles de fournisseur, d'audio ou de STT lourdes ne sont importées que si la fonctionnalité est activée.

## État des plateformes

| Plateforme | État |
| --- | --- |
| Windows 10+ | Supporté |
| macOS 13+ | Supporté* |
| Linux X11 | Supporté |
| Linux Wayland | En cours - nous travaillons sur la prise en charge de Wayland |

*Cette application n'a été testée sur macOS que pendant deux semaines de développement principal, et je ne peux plus la tester ensuite en raison d'un accès matériel limité. Si vous trouvez des bugs sur macOS, veuillez créer une issue dans ce dépôt et je ferai de mon mieux pour les corriger. Mieux encore, si vous pouvez fournir une solution, veuillez créer une pull request.

## Retours et aide aux plateformes

Les rapports de bugs sont bienvenus, en particulier pour les comportements de bureau qui dépendent des permissions OS, des gestionnaires de fenêtres, des périphériques audio ou des serveurs d'affichage. Si vous rencontrez un crash, une permission manquante, un raccourci cassé, un problème de capture, un échec de collage, ou un avertissement de vérification de configuration qui semble incorrect, veuillez ouvrir un problème avec votre version d'OS, lanceur, journaux, et l'action qui l'a déclenché.

Les journaux se trouvent dans le dossier `build_logs/`.

Nous travaillons actuellement sur la prise en charge de Linux Wayland, et l'aide pour la tester ou l'améliorer est particulièrement utile. Les tests de la prise en charge macOS sont aussi bienvenus ; ces plateformes ont le plus de cas limites d'intégration native, donc les rapports réels de différentes machines, environnements de bureau et états de permission rendent Wisp meilleur pour tout le monde.

Si vous voulez soutenir ce projet et sa mission plus large, vous pouvez contribuer directement au développement ou faire un don [ici](https://buymeacoffee.com/sunnylich).

<details>
<summary>Documentation des contributeurs</summary>

- [README développeur](../docs/DEVELOPER_README.md) — configuration, points d'entrée d'exécution, vérifications et notes de débogage.
- [Vue d'ensemble du code](../docs/OVERVIEW.md) — propriété des sous-systèmes et limites d'exécution.
- [Guide des extensions](../addons/README.md) — manifeste d'extension, permissions, hooks, outils, raccourcis et empaquetage.
- [Construire un EXE](../docs/BUILDING_EXE.md) — notes d'empaquetage Windows.

</details>



## Sources d'API de modèles gratuites

Wisp est gratuit, et vous pouvez aussi maintenir vos coûts de modèle à zéro. Plusieurs fournisseurs offrent un niveau vraiment gratuit, des crédits mensuels gratuits, ou un accès limité en débit sans coût. Wisp atteint la plupart d'entre eux via son client compatible OpenAI — quelques-uns ont une valeur `LLM_PROVIDER` dédiée, et le reste fonctionne via le point de terminaison `custom` en pointant `CUSTOM_BASE_URL` vers l'URL compatible OpenAI du fournisseur. Ajoutez la clé elle-même dans **Paramètres → LLM**.

| Fournisseur | Ce qui est gratuit | Bon pour |
| --- | --- | --- |
| OpenRouter | Modèles `:free` — ~20 req/min et 50/jour sans crédits, 1 000/jour après un rechargement unique de 10 $ ; plus un routeur `openrouter/free` | Option la plus simple "une API, plusieurs modèles" |
| Google AI Studio | Niveau gratuit de l'API Gemini dans les régions supportées, avec limites de débit | Travail multimodal et à long contexte, y compris la vision |
| Mistral | Niveau expérimental gratuit sur La Plateforme, limité en débit | Modèles européens conformes RGPD et appel de fonctions |
| NVIDIA | Accès API gratuit à de nombreux modèles ouverts via le catalogue NVIDIA API | Essayer de nombreux modèles à poids ouvert sur des points de terminaison hébergés rapides |
| GroqCloud | Niveau gratuit avec limites de débit | Inférence très rapide pour les modèles ouverts comme Llama et Qwen |
| Cerebras Inference | Niveau API gratuit pour les modèles hébergés par Cerebras | Inférence de texte extrêmement rapide et prototypage |
| GitHub Models | Accès sans coût limité en débit pour chaque compte GitHub | Prototypage, expériences, flux de travail intégrés GitHub |
| Hugging Face Inference Providers | Crédits mensuels gratuits (actuellement ~0,10 $/mois pour les utilisateurs gratuits) | Essayer de nombreux modèles ouverts via un écosystème |
| Cloudflare Workers AI | Plan gratuit Workers avec une allocation quotidienne gratuite | Applications déjà sur Cloudflare ; points de terminaison AI sans serveur |
| Vercel AI Gateway | Niveau gratuit avec 5 $/mois de crédit de passerelle pour les modèles éligibles | Projets Next.js/Vercel ; accès compatible OpenAI unifié |
| SambaNova Cloud | 5 $ de crédit API gratuit, sans carte de crédit requise | Inférence rapide de modèles ouverts hébergés |
| Puter.js | Accès JS front-end à de nombreux modèles sans votre propre clé API | Applications navigateur et démos ; pas un fournisseur backend Wisp |
| [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (passerelle locale) | Routeur open source que vous exécutez localement ; regroupe plusieurs comptes fournisseurs et offres gratuites derrière un point de terminaison compatible OpenAI, avec routage, bascule et compression optionnelle | Routez Wisp via OmniRoute avec le point de terminaison personnalisé : `LLM_PROVIDER=custom`, `CUSTOM_BASE_URL=http://localhost:20128/v1`, un modèle comme `auto`, et la clé API du tableau de bord OmniRoute |
| Local — Ollama / LM Studio / vLLM | Gratuit quand vous exécutez le modèle vous-même | Confidentialité, pas de facturation par token, points de terminaison locaux compatibles OpenAI |

Les niveaux gratuits sont limités en débit et changent souvent, donc ajoutez au moins une route de secours, et évitez d'envoyer du contexte sensible à des fournisseurs qui pourraient s'entraîner sur vos invites (la suppression de Wisp s'applique toujours). Pour le guide complet de connexion et les mises en garde, voir la page **Sources d'API gratuites** sur le [site de documentation Wisp](../Wisp%20Website/Wisp%20Docs.html).

## Licence

MIT
