# Wisp App Function Inventory

Snapshot: 2026-07-20

## Scope

This is an inventory of user-visible actions, settings, and operational capabilities in the current app. It includes small controls such as **Test Chat model**, **change shortcut**, **Pin conversation**, and **Copy log**. It does not list every internal Python helper or automated test.

The checkboxes are inventory/test-map entries. An unchecked box does **not** mean the function is broken or unfinished. Every entry cites function-specific, atomic failure references; these are risks to investigate, not claims that the function is currently broken. Reference numbers are not shared between functions unless a future code-path audit proves the failure mechanism is identical. Some entries require an optional package, an add-on, a provider account, or a particular operating system.

## 1. Launch, setup, and desktop shell

- [ ] Launch the packaged Wisp app. [1][2][3][4][5][6][7][8][9]
- [ ] Launch Wisp from the source checkout and development launchers. [10][11][12][13][14][15][16][17][18]
- [ ] Prevent a second Wisp instance from running at the same time. [19][20][21][22][23][24][25][26][27]
- [ ] Run first-start setup and dependency checks. [28][29][30][31][32][33][34][35][36]
- [ ] Show the guided profile/setup wizard again from Settings. [37][38][39][40][41][42]
- [ ] Move backward and forward through setup steps. [43][44][45][46][47][48]
- [ ] Choose the app display language during setup. [49][50][51][52][53][54]
- [ ] Choose the assistant reply language during setup. [55][56][57][58][59][60]
- [ ] Choose System, Light, or Dark appearance during setup. [61][62][63][64][65][66]
- [ ] Choose an AI provider, model, endpoint, and API key during setup. [67][68][69][70][71][72]
- [ ] Sign in with a ChatGPT Plus/Pro account during setup. [73][74][75][76][77][78][79]
- [ ] Choose no TTS, local TTS, or cloud TTS during setup. [80][81][82][83][84][85]
- [ ] Choose no STT, local Whisper, or cloud/live voice during setup. [86][87][88][89][90][91]
- [ ] Open a new chat automatically when setup finishes. [92][93][94][95][96][97][98]
- [ ] Show first-use guidance for trying the main shortcut. [99][100][101][102][103][104]
- [ ] Start the UI, brain/model, audio, and platform workers. [105][106][107][108][109][110][111]
- [ ] Display a floating always-on-top Wisp icon. [112][113][114][115][116][117][118]
- [ ] Show idle, listening, thinking, and speaking icon states. [119][120][121][122][123][124]
- [ ] Drag the floating icon to another screen position. [125][126][127][128][129][130][131]
- [ ] Auto-hide the icon when Wisp is inactive. [132][133][134][135][136][137][138]
- [ ] Show or hide the icon from the tray menu. [139][140][141][142][143][144][145]
- [ ] Open the most recent chat from the tray menu. [146][147][148][149][150]
- [ ] Open Memory from the tray menu. [151][152][153][154][155]
- [ ] Open the Addon Manager from the tray menu. [156][157][158][159][160][161][162][163]
- [ ] Open Settings from the tray menu. [164][165][166][167][168]
- [ ] Open ChatGPT or Claude controls from the provider badge. [169][170][171][172][173]
- [ ] Open runtime status where the platform worker host supports it. [174][175][176][177][178][179][180][181][182]
- [ ] Quit Wisp from the tray menu. [183][184][185][186][187]
- [ ] Start Wisp automatically when the user signs in. [188][189][190][191][192][193][194][195][196]
- [ ] Shut workers down cleanly when Wisp exits. [197][198][199][200][201][202][203][204][205]

## 2. Main ask, explain, fix, and rewrite actions

- [ ] Capture the text currently selected in another application. [206][207][208][209][210][211][212]
- [ ] Open the general intent picker over the current application. [213][214][215][216][217][218]
- [ ] Run the built-in **What is this?** action. [219][220][221][222][223][224][225]
- [ ] Run the built-in **Explain simply** action. [226][227][228][229][230][231][232]
- [ ] Run the built-in **How do I fix this?** action. [233][234][235][236][237][238][239]
- [ ] Open the rewrite/paste intent picker. [240][241][242][243][244]
- [ ] Run the built-in **Fix grammar** action. [245][246][247][248][249][250][251]
- [ ] Run the built-in **Simplify** action. [252][253][254][255][256][257][258]
- [ ] Run the built-in **Improve tone** action. [259][260][261][262][263][264][265]
- [ ] Type and submit a custom prompt from the intent picker. [266][267][268][269][270][271][272]
- [ ] Choose an action with its assigned single-key shortcut. [273][274][275][276][277][278]
- [ ] Choose an action by clicking it. [279][280][281][282][283][284][285]
- [ ] Cancel an intent with Escape. [286][287][288][289][290][291]
- [ ] Automatically close an abandoned intent picker after its configured timeout. [292][293][294][295]
- [ ] Keep the intent picker open until a choice is made by setting timeout to zero. [296][297][298][299]
- [ ] Paste a rewrite result back into the application that originally had focus. [300][301][302][303][304]
- [ ] Keep an answer in Wisp without pasting it back. [305][306][307][308][309][310][311]
- [ ] Run localized built-in intent labels and prompts for supported assistant languages. [312][313][314][315][316][317]
- [ ] Stream the model response instead of waiting for the entire answer. [318][319][320][321][322][323][324]
- [ ] Route a request through the selected primary model and configured fallbacks. [325][326][327][328][329][330][331]
- [ ] Show a useful error/recovery recommendation when a request fails. [332][333][334][335][336][337][338]
- [ ] Cancel an in-progress request. [339][340][341][342]

## 3. Shortcut management

- [ ] Use the default general picker shortcut: `Ctrl+Q` on Windows or `Ctrl+Alt+Space` on macOS/Linux. [343][344][345][346][347][348]
- [ ] Use the default rewrite picker shortcut: `Ctrl+Shift+Q` on Windows or `Ctrl+Alt+Shift+Space` on macOS/Linux. [349][350][351][352][353][354]
- [ ] Use the default screen snip shortcut: `Ctrl+Alt+Q`. [355][356][357][358][359][360]
- [ ] Use the default add-selection-to-context shortcut: `Alt+Q`. [361][362][363][364][365][366]
- [ ] Use the default clear-context shortcut: `Alt+W`. [367][368][369][370][371][372]
- [ ] Use the default read-selection-aloud shortcut: `F7`. [373][374][375][376][377][378]
- [ ] Hold the default voice-query shortcut `F9`, speak, and release to ask. [379][380][381][382][383][384]
- [ ] Toggle live voice with `Shift+F9`. [385][386][387][388][389][390]
- [ ] Hold the default dictation shortcut `F8`, speak, and release to paste. [391][392][393][394][395][396]
- [ ] Search the shortcut list by action name or description. [397][398][399][400][401][402]
- [ ] Enable or disable each shortcut independently. [403][404][405][406][407][408]
- [ ] Click a shortcut field and record a replacement key combination. [409][410][411][412][413][414]
- [ ] Clear or cancel a shortcut assignment. [415][416][417][418][419][420]
- [ ] Assign two alternate shortcuts to the same action. [421][422][423][424][425][426]
- [ ] Detect and warn about conflicting shortcuts. [427][428][429][430][431][432]
- [ ] Add a new intent shortcut/caller. [433][434][435][436][437][438]
- [ ] Rename an intent shortcut. [439][440][441][442][443][444][445]
- [ ] Remove an intent shortcut. [446][447][448][449][450][451]
- [ ] Customize an intent shortcut's action choices. [452][453][454][455][456][457]
- [ ] Add an action choice with its own key, label, and model prompt. [458][459][460][461]
- [ ] Remove an action choice. [462][463][464][465]
- [ ] Change an action choice's key, label, or model prompt. [466][467][468][469][470]
- [ ] Configure the custom-prompt action and its key. [471][472][473][474]
- [ ] Enable or disable paste-back for an intent shortcut. [475][476][477][478][479][480]
- [ ] Configure context sources separately for every intent shortcut. [481][482][483][484][485][486]
- [ ] Configure allowed model tools separately for every intent shortcut. [487][488][489][490][491][492]
- [ ] Configure context and allowed tools for voice queries. [493][494][495][496][497][498][499]
- [ ] Configure context and allowed tools for screen-snip queries. [500][501][502][503][504][505][506]
- [ ] Set dictation to raw transcript or LLM-cleaned transcript. [507][508][509][510]

## 4. Context collection and capture

- [ ] Add selected text to a persistent context buffer. [511][512][513][514][515][516]
- [ ] Clear every item from the context buffer. [517][518][519][520][521][522][523]
- [ ] Remove one context item without clearing the rest. [524][525][526][527][528][529][530]
- [ ] Re-enable a context source that was removed or turned off. [531][532][533][534][535][536][537]
- [ ] Paste clipboard items into the intent overlay as context. [538][539][540][541][542]
- [ ] Drop files or images onto Wisp as context. [543][544][545][546][547][548]
- [ ] Preview enabled context before sending. [549][550][551][552][553][554][555]
- [ ] Show context-source state and token estimates where available. [556][557][558][559][560]
- [ ] Toggle context sources with the numbered overlay keys. [561][562][563][564][565][566]
- [ ] Include nearby application/window/focused-control context. [567][568][569][570][571][572]
- [ ] Include supported open-document content. [573][574][575][576][577][578]
- [ ] Include current clipboard text. [579][580][581][582][583]
- [ ] Include current selected text. [584][585][586][587][588][589]
- [ ] Include the current browser page. [590][591][592][593][594][595]
- [ ] Search the web when the model needs current information. [596][597][598][599][600][601]
- [ ] Retrieve a specific website/page for the model. [602][603][604][605][606][607]
- [ ] Include local Git status and diff. [608][609][610][611][612]
- [ ] Fetch GitHub repository metadata. [613][614][615][616][617][618][619]
- [ ] Fetch a GitHub issue or pull request by number. [620][621][622][623][624][625][626]
- [ ] Retrieve relevant long-term memory. [627][628][629][630][631][632][633][634]
- [ ] Capture a screenshot immediately with the prompt. [635][636][637][638][639][640]
- [ ] Let the model request a screenshot only when needed. [641][642][643][644][645][646]
- [ ] Let the model request open documents only when needed. [647][648][649][650][651][652]
- [ ] Let the model request browser/web context only when needed. [653][654][655][656][657][658]
- [ ] Let the model request Git/GitHub context only when needed. [659][660][661][662][663][664][665]
- [ ] Let the model search memory only when needed. [666][667][668][669][670][671][672]
- [ ] Disable each context source for a particular shortcut or conversation. [673][674][675][676][677][678]
- [ ] Set local file access to Off. [679][680][681][682][683][684][685]
- [ ] Set local file access to Read only. [686][687][688][689][690][691][692]
- [ ] Set local file access to Ask before writing. [693][694][695][696][697][698][699]
- [ ] Set local file access to Write automatically. [700][701][702][703][704][705][706]
- [ ] Limit file access to configured root folders. [707][708][709][710][711][712][713]
- [ ] Block private files with configurable glob patterns. [714][715][716][717][718][719][720]
- [ ] Limit browser, ambient-document, and tool-document context sizes. [721][722][723][724][725][726]

## 5. Screen snipping and vision

- [ ] Open the full-screen snip overlay. [727][728][729][730][731][732]
- [ ] Draw a rectangular screen region and attach it. [733][734][735][736][737][738]
- [ ] Capture the full screen. [739][740][741][742][743][744]
- [ ] Capture the current application/window bounds. [745][746][747][748][749][750]
- [ ] Switch between Area, App, and Full capture modes. [751][752][753][754][755][756]
- [ ] Cancel capture with Escape. [757][758][759][760][761][762]
- [ ] Attach the resulting image to the intent picker. [763][764][765][766][767][768]
- [ ] Ask a vision-capable model about the captured image. [769][770][771][772][773][774]
- [ ] Route images through the configured Image model and its fallbacks. [775][776][777][778][779][780]
- [ ] Show an image returned by a model in the reply bubble or chat. [781][782][783][784][785][786]

## 6. Floating reply bubble

- [ ] Show listening, transcript, progress, answer, warning, and error text beside the icon. [787][788][789][790][791]
- [ ] Stream answer text into the bubble. [792][793][794][795][796]
- [ ] Reveal words progressively. [797][798][799][800][801]
- [ ] Highlight the currently spoken word when timestamps are available. [802][803][804][805][806][807][808][809][810][811]
- [ ] Use normal timed reveal when the TTS provider has no word timestamps. [812][813][814][815]
- [ ] Hold the fast-forward control to speed text and speech. [816][817][818][819][820]
- [ ] Stop/cancel the current reply from the close/stop control. [821][822][823][824]
- [ ] Dismiss a non-cancellable informational notice. [825][826][827][828]
- [ ] Click the bubble to open the full chat. [829][830][831][832][833][834][835]
- [ ] Drag the bubble/icon group. [836][837][838][839][840]
- [ ] Pause auto-hide while the user hovers or interacts. [841][842][843][844][845][846][847]
- [ ] Wheel-scroll long bubble text. [848][849][850][851][852][853][854]
- [ ] Snap manual scrolling back to the spoken/highlighted line. [855][856][857][858][859]
- [ ] Select text inside the bubble. [860][861][862][863][864]
- [ ] Copy selected bubble text. [865][866][867][868][869]
- [ ] Copy the full bubble text. [870][871][872][873][874]
- [ ] Display assistant-created images. [875][876][877][878][879][880]
- [ ] Edit or delete UI Lab labels from selected bubble text when that add-on is available. [881][882][883][884][885][886][887][888]

## 7. Full chat and projects

- [ ] Open persistent multi-turn chat. [889][890][891][892][893][894][895]
- [ ] Start a new chat from the button or `Ctrl+N`. [896][897][898][899][900][901]
- [ ] Send with Enter and insert a newline with Shift+Enter. [902][903][904][905][906][907][908]
- [ ] Stream assistant text, reasoning summaries, tool activity, and images. [909][910][911][912][913][914][915]
- [ ] Continue a prior conversation. [916][917][918][919][920][921][922]
- [ ] Choose whether an overlay request starts a new chat or continues an existing chat. [923][924][925][926][927][928][929]
- [ ] Switch conversations from history. [930][931][932][933][934][935][936]
- [ ] Group conversation history by project. [937][938][939][940][941][942][943]
- [ ] Create a project. [944][945][946][947][948][949][950]
- [ ] Choose the project for new chats. [951][952][953][954][955][956][957]
- [ ] Scope memory to the selected project. [958][959][960][961][962][963][964]
- [ ] Add a conversation to a project. [965][966][967][968][969][970][971]
- [ ] Pin or unpin a conversation. [972][973][974][975][976][977][978]
- [ ] Rename a conversation. [979][980][981][982][983][984][985]
- [ ] Delete a conversation after confirmation. [986][987][988][989][990][991][992]
- [ ] Browse files associated with a conversation. [993][994][995][996][997][998][999]
- [ ] Show conversation and message timestamps. [1000][1001][1002][1003][1004][1005][1006]
- [ ] Attach one or more files or images with the file picker. [1007][1008][1009][1010][1011][1012]
- [ ] Drag and drop files/images into chat. [1013][1014][1015][1016][1017][1018]
- [ ] Show pending attachment names and context. [1019][1020][1021][1022][1023][1024]
- [ ] Display attached and returned image thumbnails. [1025][1026][1027][1028][1029][1030]
- [ ] Configure App context per conversation. [1031][1032][1033][1034][1035][1036][1037]
- [ ] Configure Browser/Web context per conversation. [1038][1039][1040][1041][1042][1043]
- [ ] Configure Selection context per conversation. [1044][1045][1046][1047][1048][1049][1050]
- [ ] Configure Clipboard context per conversation. [1051][1052][1053][1054][1055]
- [ ] Configure Screenshot context per conversation. [1056][1057][1058][1059][1060][1061]
- [ ] Configure Git/GitHub context per conversation. [1062][1063][1064][1065][1066][1067][1068]
- [ ] Configure Memory context per conversation. [1069][1070][1071][1072][1073][1074][1075]
- [ ] Configure Files access per conversation. [1076][1077][1078][1079][1080][1081][1082]
- [ ] Capture selection or screenshot interactively when enabling its chat context chip. [1083][1084][1085][1086][1087][1088]
- [ ] Preview context token estimates before sending. [1089][1090][1091][1092][1093]
- [ ] Copy selected text from a message. [1094][1095][1096][1097][1098][1099]
- [ ] Branch a new conversation from any retained message. [1100][1101][1102][1103][1104][1105][1106]
- [ ] Rewind the current conversation to any retained message. [1107][1108][1109][1110][1111][1112][1113]
- [ ] Edit or delete UI Lab labels from selected chat text when that add-on is available. [1114][1115][1116][1117][1118][1119][1120][1121]
- [ ] Zoom chat text with the supported keyboard/wheel controls. [1122][1123][1124][1125][1126][1127][1128]
- [ ] Show model tool-loop trace when enabled. [1129][1130][1131][1132][1133][1134][1135]
- [ ] Split longer answers into planned chunks when enabled. [1136][1137][1138][1139][1140][1141][1142]
- [ ] Set the number of planned chunks and the minimum prompt length. [1143][1144][1145][1146][1147][1148][1149]
- [ ] Set chat reasoning effort. [1150][1151][1152][1153][1154][1155][1156]
- [ ] Auto-elaborate the latest short answer when opening chat. [1157][1158][1159][1160][1161][1162][1163]
- [ ] Customize the auto-elaboration prompt. [1164][1165][1166][1167][1168][1169][1170]

## 8. External ChatGPT and Claude conversations

- [ ] Pull local ChatGPT/Codex and Claude Code transcripts into Wisp. [1171][1172][1173][1174][1175][1176][1177][1178][1179]
- [ ] Report imported, updated, and unchanged transcript counts. [1180][1181][1182][1183][1184][1185][1186][1187][1188]
- [ ] Keep Wisp, ChatGPT, and Claude conversation namespaces distinct. [1189][1190][1191][1192][1193][1194][1195][1196][1197]
- [ ] Continue a conversation using Wisp's own model engine. [1198][1199][1200][1201][1202][1203][1204][1205][1206]
- [ ] Continue a conversation using the selected ChatGPT/Codex or Claude agent. [1207][1208][1209][1210][1211][1212][1213][1214][1215]
- [ ] Choose whether continued messages belong to Wisp or the selected agent. [1216][1217][1218][1219][1220][1221][1222][1223][1224]
- [ ] Push new Wisp turns back into their source transcript after confirmation. [1225][1226][1227][1228][1229][1230][1231][1232][1233]
- [ ] Create a backup before editing an external transcript. [1234][1235][1236][1237][1238][1239][1240][1241][1242]
- [ ] Export a Wisp conversation as a new ChatGPT conversation. [1243][1244][1245][1246][1247][1248][1249][1250][1251]
- [ ] Export a Wisp conversation as a new Claude conversation. [1252][1253][1254][1255][1256][1257][1258][1259][1260]
- [ ] Open provider controls from the floating provider badge. [1261][1262][1263][1264][1265][1266][1267][1268][1269]
- [ ] Select provider-default or explicit agent model. [1270][1271][1272][1273][1274][1275][1276][1277][1278]
- [ ] Choose or automatically detect the agent project/workspace folder. [1279][1280][1281][1282][1283][1284][1285][1286][1287]
- [ ] Enable agent fast mode. [1288][1289][1290][1291][1292][1293][1294][1295][1296]
- [ ] Choose agent reasoning effort: provider default, low, medium, high, xhigh, max, or ultra where supported. [1297][1298][1299][1300][1301][1302][1303][1304][1305]
- [ ] Choose detailed, concise, provider, or no visible reasoning summaries. [1306][1307][1308][1309][1310][1311][1312][1313][1314]
- [ ] Require approval for agent operations. [1315][1316][1317][1318][1319][1320][1321][1322][1323]
- [ ] Allow agent edits within the selected project. [1324][1325][1326][1327][1328][1329][1330][1331][1332]
- [ ] Grant full agent access. [1333][1334][1335][1336][1337][1338][1339][1340][1341]
- [ ] Use plan-only/read-only agent mode. [1342][1343][1344][1345][1346][1347][1348][1349][1350]

## 9. Provider accounts and connections

- [ ] Sign in to ChatGPT Plus/Pro in a browser. [1351][1352][1353][1354][1355][1356][1357]
- [ ] Check ChatGPT sign-in status. [1358][1359][1360][1361][1362]
- [ ] Sign out of ChatGPT. [1363][1364][1365][1366]
- [ ] Sign in to GitHub with the device/browser OAuth flow. [1367][1368][1369][1370][1371][1372][1373]
- [ ] Check GitHub sign-in status. [1374][1375][1376][1377][1378]
- [ ] Sign out of GitHub. [1379][1380][1381][1382]
- [ ] Override the GitHub OAuth client ID and scopes. [1383][1384][1385][1386][1387][1388][1389]
- [ ] Connect and clear GitHub Copilot credentials. [1390][1391][1392][1393]
- [ ] Test the GitHub Copilot connection. [1394][1395][1396][1397][1398]
- [ ] Add a provider connection. [1399][1400][1401][1402][1403][1404]
- [ ] Give a connection an alias. [1405][1406][1407][1408][1409][1410]
- [ ] Store API keys in the operating-system keychain. [1411][1412][1413][1414][1415][1416]
- [ ] Remove/clear a provider connection. [1417][1418][1419][1420][1421][1422]
- [ ] Search connections by provider or alias. [1423][1424][1425][1426][1427][1428][1429]
- [ ] Filter All, Cloud, or Local/custom connections. [1430][1431][1432][1433][1434]
- [ ] Expand or collapse large connection lists. [1435][1436][1437][1438][1439]
- [ ] Configure a custom OpenAI-compatible base URL and API key. [1440][1441][1442][1443][1444][1445]
- [ ] Pick a saved custom endpoint from the Endpoints menu. [1446][1447][1448][1449][1450][1451]
- [ ] Use an existing local Ollama installation and auto-start its server when needed. [1452][1453][1454][1455][1456][1457][1458]
- [ ] Use an LM Studio or other OpenAI-compatible endpoint through Custom. [1459][1460][1461][1462][1463][1464][1465]
- [ ] Refresh model names from providers that support model listing. [1466][1467][1468][1469][1470]
- [ ] Enter an exact model name manually. [1471][1472][1473][1474][1475]

### Supported connection/provider choices

- [ ] Groq. [1476][1477][1478][1479][1480][1481][1482]
- [ ] OpenAI API. [1483][1484][1485][1486][1487][1488][1489]
- [ ] Anthropic. [1490][1491][1492][1493][1494][1495][1496]
- [ ] Google AI Studio. [1497][1498][1499][1500][1501][1502][1503]
- [ ] ChatGPT Plus/Pro OAuth. [1504][1505][1506][1507][1508][1509][1510]
- [ ] GitHub Copilot. [1511][1512][1513][1514][1515][1516][1517]
- [ ] DeepSeek. [1518][1519][1520][1521][1522][1523][1524]
- [ ] OpenRouter. [1525][1526][1527][1528][1529][1530][1531]
- [ ] Mistral. [1532][1533][1534][1535][1536][1537][1538]
- [ ] xAI/Grok. [1539][1540][1541][1542][1543][1544][1545]
- [ ] Together AI. [1546][1547][1548][1549][1550][1551][1552]
- [ ] Cerebras. [1553][1554][1555][1556][1557][1558][1559]
- [ ] Z.AI/GLM. [1560][1561][1562][1563][1564][1565][1566]
- [ ] NVIDIA. [1567][1568][1569][1570][1571][1572][1573]
- [ ] SambaNova. [1574][1575][1576][1577][1578][1579][1580]
- [ ] GitHub Models. [1581][1582][1583][1584][1585][1586][1587]
- [ ] Hugging Face. [1588][1589][1590][1591][1592][1593][1594]
- [ ] Chutes. [1595][1596][1597][1598][1599][1600][1601]
- [ ] Vercel AI Gateway. [1602][1603][1604][1605][1606][1607][1608]
- [ ] Fireworks. [1609][1610][1611][1612][1613][1614][1615]
- [ ] Cohere. [1616][1617][1618][1619][1620][1621][1622]
- [ ] AI21. [1623][1624][1625][1626][1627][1628][1629]
- [ ] Nebius. [1630][1631][1632][1633][1634][1635][1636]
- [ ] Ollama local. [1637][1638][1639][1640][1641][1642][1643][1644][1645]
- [ ] Custom OpenAI-compatible provider. [1646][1647][1648][1649][1650]

## 10. Model routing and model tests

- [ ] Choose the primary provider/model for the Chat model route. [1651][1652][1653][1654][1655][1656][1657]
- [ ] Add, remove, and reorder Chat model fallbacks. [1658][1659][1660][1661][1662][1663][1664]
- [ ] **Test Chat model** from Settings. [1665][1666][1667][1668][1669][1670][1671]
- [ ] Choose the primary provider/model for the Image model route. [1672][1673][1674][1675][1676][1677][1678]
- [ ] Add, remove, and reorder Image model fallbacks. [1679][1680][1681][1682][1683][1684][1685]
- [ ] **Test Image model** from Settings. [1686][1687][1688][1689][1690][1691][1692]
- [ ] Choose the primary provider/model for the Memory model route. [1693][1694][1695][1696][1697][1698][1699]
- [ ] Add, remove, and reorder Memory model fallbacks. [1700][1701][1702][1703][1704][1705][1706]
- [ ] **Test Memory model** from Settings. [1707][1708][1709][1710][1711][1712][1713]
- [ ] Copy one route's provider/model rows to all model routes with **Apply to all**. [1714][1715][1716][1717][1718][1719][1720]
- [ ] Drag model rows to change priority. [1721][1722][1723][1724][1725][1726][1727]
- [ ] Refresh available model IDs for an individual route row. [1728][1729][1730][1731][1732]
- [ ] Fall through to the next configured model when a route fails. [1733][1734][1735][1736][1737][1738][1739]
- [ ] Temporarily cool down a failing route before retrying it later. [1740][1741][1742][1743][1744][1745][1746]
- [ ] Adapt to provider differences such as streaming, tools, images, token parameters, and reasoning controls. [1747][1748][1749][1750][1751][1752][1753]
- [ ] Show warnings when a chosen model/provider cannot satisfy an enabled capability. [1754][1755][1756][1757][1758][1759][1760]
- [ ] Use Wisp, ChatGPT, or Claude Agent as the conversation execution engine. [1761][1762][1763][1764][1765][1766][1767]
- [ ] Choose Wisp or the selected agent as conversation owner. [1768][1769][1770][1771][1772][1773][1774]
- [ ] Edit separate system prompts for Wisp, ChatGPT, and Claude conversations. [1775][1776][1777][1778][1779][1780][1781]

## 11. Text to speech, speech to text, dictation, and live voice

- [ ] Disable text to speech while retaining manual read-aloud/test features. [1782][1783][1784][1785][1786][1787][1788][1789][1790][1791]
- [ ] Automatically speak assistant replies. [1792][1793][1794][1795][1796][1797][1798][1799][1800][1801]
- [ ] Read selected text aloud on demand. [1802][1803][1804][1805][1806][1807]
- [ ] Stop current speech playback. [1808][1809][1810][1811][1812][1813][1814][1815][1816][1817]
- [ ] Set global TTS playback volume. [1818][1819][1820][1821][1822][1823][1824][1825][1826][1827]
- [ ] Set normal and held/fast-forward TTS playback speed. [1828][1829][1830][1831][1832][1833][1834][1835][1836][1837]
- [ ] Configure read-aloud minimum and maximum chunk size. [1838][1839][1840][1841][1842][1843][1844][1845][1846][1847]
- [ ] Test the selected TTS provider with **Test TTS**. [1848][1849][1850][1851][1852][1853][1854]
- [ ] Use Cartesia TTS and configure its key and voice ID. [1855][1856][1857][1858][1859][1860][1861][1862][1863][1864]
- [ ] Install and use ElevenLabs TTS; configure key, voice, and model. [1865][1866][1867][1868][1869][1870][1871]
- [ ] Use OpenAI TTS; configure voice and model. [1872][1873][1874][1875][1876][1877][1878][1879][1880][1881]
- [ ] Use an OpenAI-compatible `/audio/speech` endpoint; configure URL, key, voice, model, and sample rate. [1882][1883][1884][1885][1886][1887][1888][1889][1890][1891]
- [ ] Use a local GPT-SoVITS server; configure reference audio/transcript/languages and sample rate. [1892][1893][1894][1895][1896][1897][1898][1899][1900][1901]
- [ ] Install and use local Kokoro TTS. [1902][1903][1904][1905][1906][1907][1908]
- [ ] Configure Kokoro voice, language code, device, speed, and sample rate. [1909][1910][1911][1912][1913][1914][1915][1916][1917][1918]
- [ ] Download, repair, or update Kokoro voice-model assets. [1919][1920][1921][1922][1923][1924][1925]
- [ ] Choose automatic, CPU, or CUDA speech device where supported. [1926][1927][1928][1929][1930][1931][1932][1933][1934][1935]
- [ ] Install local faster-whisper speech-to-text. [1936][1937][1938][1939][1940][1941][1942]
- [ ] Choose Whisper model size. [1943][1944][1945][1946][1947][1948][1949][1950][1951][1952]
- [ ] Choose STT device and compute type. [1953][1954][1955][1956][1957][1958][1959][1960][1961][1962]
- [ ] Choose automatic or explicit speech language. [1963][1964][1965][1966][1967][1968][1969][1970][1971][1972]
- [ ] Configure Whisper beam size. [1973][1974][1975][1976][1977][1978][1979][1980][1981][1982]
- [ ] Transcribe long recordings in overlapping background chunks. [1983][1984][1985][1986][1987][1988][1989][1990][1991][1992]
- [ ] Configure first STT chunk time, cadence, live-edge delay, and overlap. [1993][1994][1995][1996][1997][1998][1999][2000][2001][2002]
- [ ] Review a voice transcript and its context before asking. [2003][2004][2005][2006][2007][2008][2009][2010][2011][2012]
- [ ] Send a voice transcript directly without review. [2013][2014][2015][2016][2017][2018][2019][2020][2021][2022]
- [ ] Dictate raw speech into the currently focused field. [2023][2024][2025][2026][2027]
- [ ] Clean dictated speech with the LLM before pasting it. [2028][2029][2030][2031][2032][2033][2034][2035][2036][2037]
- [ ] Install live-voice support. [2038][2039][2040][2041][2042][2043][2044]
- [ ] Start and stop a hands-free Gemini Live conversation. [2045][2046][2047][2048][2049][2050][2051][2052][2053][2054]
- [ ] Display live user and assistant transcripts. [2055][2056][2057][2058][2059][2060][2061][2062][2063][2064]
- [ ] Interrupt Wisp by speaking over it when full duplex is enabled. [2065][2066][2067][2068][2069][2070][2071][2072][2073][2074]
- [ ] Pause the microphone while Wisp speaks in speaker/half-duplex mode. [2075][2076][2077][2078][2079][2080][2081][2082][2083][2084]
- [ ] Choose the live-conversation provider, model, and voice. [2085][2086][2087][2088][2089][2090][2091][2092][2093][2094]

## 12. Memory

- [ ] Store durable facts with phrases such as “remember that,” “note that,” or “keep in mind.” [2095][2096][2097][2098][2099][2100][2101][2102]
- [ ] Forget/remove remembered facts through supported memory commands. [2103][2104][2105][2106][2107][2108][2109][2110]
- [ ] Let the model search memory when a prompt needs it. [2111][2112][2113][2114][2115][2116][2117][2118]
- [ ] Let the model save a durable memory when permitted. [2119][2120][2121][2122][2123][2124][2125][2126]
- [ ] Automatically extract long-term facts from conversations. [2127][2128][2129][2130][2131][2132][2133][2134]
- [ ] Automatically consolidate memory on a configurable interval. [2135][2136][2137][2138][2139][2140][2141][2142]
- [ ] Set how many facts are retrieved for a query. [2143][2144][2145][2146][2147][2148][2149][2150]
- [ ] Set the short-term-memory token budget before compression. [2151][2152][2153][2154][2155][2156][2157][2158]
- [ ] Keep General memory separate from project memory. [2159][2160][2161][2162][2163][2164][2165][2166]
- [ ] Open the Long-term Memory viewer. [2167][2168][2169][2170][2171][2172][2173][2174]
- [ ] View facts grouped by project. [2175][2176][2177][2178][2179][2180][2181][2182]
- [ ] Add a fact manually and choose its project. [2183][2184][2185][2186][2187][2188][2189]
- [ ] Click and edit a fact. [2190][2191][2192][2193][2194][2195][2196][2197]
- [ ] Move/change a fact's project. [2198][2199][2200][2201][2202][2203][2204]
- [ ] Delete a fact. [2205][2206][2207][2208][2209][2210][2211]
- [ ] Refresh the memory list. [2212][2213][2214][2215][2216][2217][2218][2219]

## 13. Privacy and secret handling

- [ ] Send messages with privacy filtering off. [2220][2221][2222][2223][2224][2225][2226]
- [ ] Redact private information with the built-in local filter. [2227][2228][2229][2230][2231][2232][2233]
- [ ] Install, repair, and use the advanced local privacy model. [2234][2235][2236][2237][2238][2239][2240]
- [ ] Remove the advanced privacy model. [2241][2242][2243][2244][2245][2246][2247]
- [ ] Keep the built-in filter active alongside Advanced mode. [2248][2249][2250][2251][2252][2253][2254]
- [ ] Review detected private information and the redacted request before sending. [2255][2256][2257][2258][2259][2260][2261]
- [ ] Cancel a request from the privacy review. [2262][2263][2264][2265][2266][2267][2268]
- [ ] Apply privacy handling to normal requests, tool results, and local-model requests. [2269][2270][2271][2272][2273][2274][2275]
- [ ] Store provider credentials outside the plain settings file in the OS keychain. [2276][2277][2278][2279][2280][2281]
- [ ] Mask stored secrets in Settings. [2282][2283][2284][2285][2286][2287]
- [ ] Reset all settings and remove saved keychain secrets after confirmation. [2288][2289][2290][2291][2292][2293][2294]

## 14. Built-in model tools and permissions

- [ ] Search the web with `web_search`. [2295][2296][2297][2298][2299][2300]
- [ ] Read open documents/current pages with `get_context`. [2301][2302][2303][2304][2305][2306]
- [ ] Retrieve a specific website with `retrieve_website`. [2307][2308][2309][2310][2311][2312]
- [ ] Capture the screen with `capture_screen`. [2313][2314][2315][2316][2317][2318]
- [ ] Search long-term memory with `memory_search`. [2319][2320][2321][2322][2323][2324][2325][2326]
- [ ] Save durable memory with `memory_save`. [2327][2328][2329][2330][2331][2332]
- [ ] Read local Git status with `git_status`. [2333][2334][2335][2336][2337]
- [ ] Read local Git diff with `git_diff`. [2338][2339][2340][2341][2342]
- [ ] List allowed folders with `list_files`. [2343][2344][2345][2346][2347][2348][2349]
- [ ] Read allowed files with `read_file`. [2350][2351][2352][2353][2354][2355][2356]
- [ ] Create allowed files with `create_file`. [2357][2358][2359][2360][2361][2362][2363]
- [ ] Patch allowed files with `edit_file`. [2364][2365][2366][2367][2368][2369][2370]
- [ ] Create or overwrite allowed files with `write_file`. [2371][2372][2373][2374][2375][2376][2377]
- [ ] Fetch GitHub repository metadata with `github_repo`. [2378][2379][2380][2381][2382][2383][2384]
- [ ] Fetch a GitHub issue or pull request with `github_issue`. [2385][2386][2387][2388][2389][2390][2391]
- [ ] Set each local-file tool to Off or Auto for an individual shortcut. [2392][2393][2394][2395][2396][2397]
- [ ] Set installed/add-on tools to Off or Auto for an individual shortcut. [2398][2399][2400][2401][2402][2403]
- [ ] Group MCP tools by server and enable/disable a complete server. [2404][2405][2406][2407][2408][2409][2410][2411]
- [ ] Override an individual MCP tool or let it follow its server setting. [2412][2413][2414][2415][2416][2417][2418][2419]
- [ ] Ask for approval and show a diff before file writes when configured. [2420][2421][2422][2423][2424][2425][2426]
- [ ] Approve a proposed live file operation. [2427][2428][2429][2430][2431][2432][2433]
- [ ] Request an alternate operation and provide feedback. [2434][2435][2436][2437][2438][2439]
- [ ] Decline a proposed live file operation and provide feedback. [2440][2441][2442][2443][2444][2445][2446]
- [ ] Enforce per-request tool-call and tool-output budgets. [2447][2448][2449][2450][2451][2452]
- [ ] Show tool activity/trace in chat when enabled. [2453][2454][2455][2456][2457][2458][2459]

## 15. Add-ons and MCP extensions

- [ ] Open the Addon Manager. [2460][2461][2462][2463][2464][2465][2466][2467]
- [ ] Open the add-ons folder. [2468][2469][2470][2471]
- [ ] Install an add-on archive. [2472][2473][2474][2475][2476][2477][2478]
- [ ] Install an add-on from a folder. [2479][2480][2481][2482][2483][2484][2485]
- [ ] Enable or disable an installed add-on. [2486][2487][2488][2489][2490][2491][2492][2493]
- [ ] Open an add-on's settings. [2494][2495][2496][2497][2498][2499][2500][2501]
- [ ] Edit checkbox, number, text, and choice settings supplied by an add-on. [2502][2503][2504][2505][2506][2507][2508][2509]
- [ ] Open and review an add-on's logs. [2510][2511][2512][2513]
- [ ] Install, rebuild, or repair an add-on's isolated dependencies after approval. [2514][2515][2516][2517][2518][2519][2520]
- [ ] Run add-ons in isolated host processes. [2521][2522][2523][2524][2525][2526][2527][2528]
- [ ] Enforce permissions declared by an add-on manifest. [2529][2530][2531][2532][2533][2534][2535][2536]
- [ ] Let add-ons contribute context before a query. [2537][2538][2539][2540][2541][2542][2543][2544]
- [ ] Let add-ons contribute model-callable tools. [2545][2546][2547][2548][2549][2550][2551][2552]
- [ ] Let add-ons process a response after completion. [2553][2554][2555][2556][2557][2558][2559][2560]
- [ ] Let add-ons add intent actions and action rows. [2561][2562][2563][2564][2565][2566][2567][2568]
- [ ] Let add-ons add global shortcuts. [2569][2570][2571][2572][2573][2574]
- [ ] Let add-ons add tray actions and notifications. [2575][2576][2577][2578][2579][2580][2581][2582]
- [ ] Let add-ons add Settings fields. [2583][2584][2585][2586][2587][2588][2589][2590]
- [ ] Let add-ons perform capped auxiliary LLM actions. [2591][2592][2593][2594][2595][2596][2597][2598]
- [ ] Discover configured MCP servers and expose their tools through the bridge. [2599][2600][2601][2602][2603][2604][2605][2606]
- [ ] Expose Wisp context MCP operations for selected text, clipboard, active window, browser page, and screen snip. [2607][2608][2609][2610][2611][2612]

## 16. Multi-agent tasks

- [ ] Start an agent task from the tray menu. [2613][2614][2615][2616][2617][2618][2619][2620][2621]
- [ ] Copy task settings from the last agent task. [2622][2623][2624][2625][2626][2627][2628][2629][2630]
- [ ] Set the task title and objective. [2631][2632][2633][2634][2635][2636][2637][2638][2639]
- [ ] Copy relevant context from the current application. [2640][2641][2642][2643][2644][2645]
- [ ] Add required task context manually. [2646][2647][2648][2649][2650][2651][2652][2653][2654]
- [ ] Choose an agent provider/model and fallback models. [2655][2656][2657][2658][2659][2660][2661][2662][2663]
- [ ] Choose the task scope folder. [2664][2665][2666][2667][2668][2669][2670][2671][2672]
- [ ] Configure allowed and blocked file globs. [2673][2674][2675][2676][2677][2678][2679][2680][2681]
- [ ] Use the coordinator, builder, and reviewer defaults. [2682][2683][2684][2685][2686][2687][2688][2689][2690]
- [ ] Add or remove agents. [2691][2692][2693][2694][2695][2696][2697][2698][2699]
- [ ] Customize each agent's name, role, model, and instructions. [2700][2701][2702][2703][2704][2705][2706][2707][2708]
- [ ] Start with a parallel read-only briefing. [2709][2710][2711][2712][2713][2714][2715][2716][2717]
- [ ] Run implementer agents in parallel with file leases. [2718][2719][2720][2721][2722][2723][2724][2725][2726]
- [ ] Add or remove agent-to-agent communications. [2727][2728][2729][2730][2731][2732][2733][2734][2735]
- [ ] Configure sender, recipient, trigger, and message for a communication. [2736][2737][2738][2739][2740][2741][2742][2743][2744]
- [ ] Create paired two-way exchanges. [2745][2746][2747][2748][2749][2750][2751][2752][2753]
- [ ] Reset the communication map to defaults. [2754][2755][2756][2757][2758][2759][2760][2761][2762]
- [ ] Open and refresh the Agent Communication Map. [2763][2764][2765][2766][2767][2768][2769][2770][2771]
- [ ] Set explicit completion criteria. [2772][2773][2774][2775][2776][2777][2778][2779][2780]
- [ ] Preview the generated task specification. [2781][2782][2783][2784][2785][2786][2787][2788][2789]
- [ ] Start or cancel the configured task. [2790][2791][2792][2793][2794][2795][2796][2797][2798]
- [ ] Review and approve an agent permission request. [2799][2800][2801][2802][2803][2804][2805][2806][2807]
- [ ] Decline an agent permission request. [2808][2809][2810][2811][2812][2813][2814][2815][2816]
- [ ] Watch the live Meeting view and agent cards. [2817][2818][2819][2820][2821][2822][2823][2824][2825]
- [ ] View Live Log, Model Trace, and Final Report tabs. [2826][2827][2828][2829][2830][2831][2832][2833][2834]
- [ ] Drag/resize agent cards and reset their layout. [2835][2836][2837][2838][2839][2840][2841][2842][2843]
- [ ] Inspect agent status, health, messages, and shared-board activity. [2844][2845][2846][2847][2848][2849][2850][2851][2852]
- [ ] View the task diff. [2853][2854][2855][2856][2857][2858][2859][2860][2861]
- [ ] Open the task memory/result folder. [2862][2863][2864][2865]
- [ ] Open the task scope folder. [2866][2867][2868][2869]
- [ ] Retry a task. [2870][2871][2872][2873][2874][2875][2876][2877][2878]
- [ ] Continue a completed or interrupted task. [2879][2880][2881][2882][2883][2884][2885][2886][2887]
- [ ] Nudge a selected agent with a message. [2888][2889][2890][2891][2892][2893][2894][2895][2896]
- [ ] Pause after the current turn and resume later. [2897][2898][2899][2900][2901][2902][2903][2904][2905]
- [ ] Cancel a running task. [2906][2907][2908][2909][2910][2911][2912][2913][2914]
- [ ] Open Agent Task History. [2915][2916][2917][2918][2919][2920][2921][2922][2923]
- [ ] Review a historical task's Summary, Run Log, Model Trace, and Diff. [2924][2925][2926][2927][2928][2929][2930][2931][2932]
- [ ] Refresh history and reopen result folders. [2933][2934][2935][2936][2937][2938][2939][2940][2941]

## 17. Settings, profiles, appearance, and behavior

- [ ] Navigate General, Connections, Model routing, Voice & audio, Shortcuts, Prompts & context, Advanced, and About pages. [2942][2943][2944][2945][2946][2947]
- [ ] Search Settings and jump to matching fields. [2948][2949][2950][2951][2952][2953]
- [ ] Load a built-in configuration profile. [2954][2955][2956][2957][2958][2959]
- [ ] Create/save a custom profile. [2960][2961][2962][2963][2964][2965][2966]
- [ ] Rename a custom profile. [2967][2968][2969][2970][2971][2972][2973]
- [ ] Delete a custom profile. [2974][2975][2976][2977][2978][2979][2980]
- [ ] Run the setup check from Settings. [2981][2982][2983][2984][2985][2986]
- [ ] Run the guided profile setup from Settings. [2987][2988][2989][2990][2991][2992]
- [ ] Save settings changes. [2993][2994][2995][2996][2997][2998][2999]
- [ ] Cancel/discard settings changes. [3000][3001][3002][3003][3004]
- [ ] Reset only the current Settings page. [3005][3006][3007][3008][3009][3010]
- [ ] Reset every setting after confirmation. [3011][3012][3013][3014][3015][3016][3017]
- [ ] Choose System, Light, or Dark theme. [3018][3019][3020][3021][3022][3023]
- [ ] Customize background, surface, text, and accent colors. [3024][3025][3026][3027][3028][3029]
- [ ] Customize icon size. [3030][3031][3032][3033][3034][3035]
- [ ] Customize bubble width, line count, and font size. [3036][3037][3038][3039][3040][3041]
- [ ] Customize bubble background, text, and spoken-word highlight colors. [3042][3043][3044][3045][3046][3047]
- [ ] Enable or disable bubble wheel scrolling and spoken-line snap-back. [3048][3049][3050][3051][3052][3053]
- [ ] Set bubble text reveal speed and held fast-forward speed. [3054][3055][3056][3057][3058][3059]
- [ ] Set bubble display/auto-hide delay and scroll snap delay. [3060][3061][3062][3063][3064][3065][3066]
- [ ] Choose app UI language: system, English, Chinese, Traditional Chinese, Spanish, or French. [3067][3068][3069][3070][3071][3072]
- [ ] Choose assistant reply language or match the user's language. [3073][3074][3075][3076][3077][3078]
- [ ] Edit separate Wisp, ChatGPT, and Claude system prompts. [3079][3080][3081][3082][3083][3084][3085]

## 18. Optional installer controls

- [ ] Watch live output and elapsed time while an optional component installs. [3086][3087][3088][3089][3090][3091][3092]
- [ ] Cancel a running optional installer. [3093][3094][3095]
- [ ] Copy the installer log. [3096][3097][3098][3099][3100]
- [ ] Open the installer log folder. [3101][3102][3103][3104]
- [ ] Close the installer after it finishes. [3105][3106][3107][3108][3109][3110][3111]
- [ ] Restart Wisp immediately when an installed component requires restart. [3112][3113][3114]
- [ ] Retain a log/status file for failed or interrupted optional installs. [3115][3116][3117][3118][3119][3120][3121]

## 19. Updates, diagnostics, uninstall, and resilience

- [ ] Show the installed Wisp version. [3122][3123][3124][3125][3126][3127][3128][3129][3130]
- [ ] Check GitHub Releases for a newer packaged build. [3131][3132][3133][3134][3135][3136][3137]
- [ ] Download and apply a packaged update through the update flow. [3138][3139][3140][3141][3142][3143][3144]
- [ ] Pull the latest `origin/main` for a supported source checkout. [3145][3146][3147][3148][3149][3150][3151]
- [ ] Report update progress and errors. [3152][3153][3154][3155][3156][3157][3158][3159][3160]
- [ ] Create a bounded, redacted crash-report ZIP. [3161][3162][3163][3164][3165][3166][3167]
- [ ] Reveal the created crash report in Explorer/Finder. [3168][3169][3170][3171]
- [ ] Exclude chats, memory, settings, environment files, and keychain files from the crash bundle. [3172][3173][3174][3175][3176][3177]
- [ ] Review the crash ZIP before sharing it. [3178][3179][3180][3181][3182][3183][3184][3185][3186]
- [ ] Record runtime and crash logs for diagnosis. [3187][3188][3189][3190][3191][3192][3193][3194][3195]
- [ ] Show runtime status for supported worker-host configurations. [3196][3197][3198][3199][3200][3201][3202][3203][3204]
- [ ] Recover or recommend action after worker/model/audio failures. [3205][3206][3207][3208][3209]
- [ ] Confirm the exact uninstall plan before removal. [3210][3211][3212][3213][3214][3215][3216][3217][3218]
- [ ] Uninstall the app, its data, settings, chats, memory, add-ons, tools, logs, updates, optional packages, and Wisp-owned local AI assets. [3219][3220][3221][3222][3223][3224][3225]
- [ ] Remove a source checkout only when that checkout is explicitly included in the confirmed uninstall plan. [3226][3227][3228][3229][3230][3231][3232]
- [ ] Run the uninstaller as a detached self-removing process after Wisp closes. [3233][3234][3235][3236][3237][3238][3239]

## 20. Cross-platform desktop integration

- [ ] Register global hotkeys on Windows, macOS, and Linux through platform-specific backends. [3240][3241][3242][3243][3244][3245]
- [ ] Read selected/focused text using native accessibility/UI APIs where available. [3246][3247][3248][3249][3250][3251]
- [ ] Read and restore clipboard content safely around capture/paste operations. [3252][3253][3254][3255][3256]
- [ ] Paste generated or dictated text back into the previously focused application. [3257][3258][3259][3260][3261]
- [ ] Capture screen/app regions on multi-monitor desktops. [3262][3263][3264][3265][3266][3267]
- [ ] Use macOS helper/Accessibility flows where native permissions are required. [3268][3269][3270][3271][3272][3273]
- [ ] Use Linux AT-SPI/X11/Wayland-compatible fallbacks where available. [3274][3275][3276][3277][3278][3279]
- [ ] Use Windows native focused-control, clipboard, and hotkey integrations. [3280][3281][3282][3283][3284][3285]
- [ ] Keep windows on screen and provide standard close/minimize controls where appropriate. [3286][3287][3288][3289][3290][3291][3292]
- [ ] Use the operating-system file browser to reveal logs, reports, task folders, add-ons, and conversation files. [3293][3294][3295][3296]

## Audit notes

- This inventory was derived from the current application UI, configuration surfaces, launch/setup flows, query pipeline, model clients, tool registry, voice stack, memory store, add-on manager, and agent task windows.
- “All functions” here means everything a user can deliberately invoke or configure, plus major automatic behavior that changes what the app does.
- Developer-only test runners, build scripts, packaging scripts, and individual internal Python methods are intentionally outside this app-function list.
- Dynamic add-ons and MCP servers can introduce additional actions and tools at runtime; their extension points and management actions are included above, but unknown third-party tool names cannot be listed until installed.

## Differentiated failure reference catalogue

Each reference belongs to one specific function. Similar wording under different functions has a different number, so category-level similarity is never treated as proof of an identical failure mechanism.

Coverage marker: **[x]** means a test directly triggers this numbered failure path and asserts its result. A plain numbered cause has no direct failure-path assertion found. **[T###]** points to the exact test below. Happy-path and merely related tests do not count.

### Launch the packaged Wisp app.

- [1] The Wisp installation is missing.
- [2] The Wisp installation is corrupt.
- [3] The required runtime is missing.
- [4] The required runtime is corrupt.
- [5] A stale instance lock remains.
- [6] Required OS permission is absent.
- [x] [7] A worker crashes. [T001]
- [8] Startup is interrupted.
- [9] Shutdown is interrupted.

### Launch Wisp from the source checkout and development launchers.

- [10] The Wisp installation is missing.
- [11] The Wisp installation is corrupt.
- [12] The required runtime is missing.
- [13] The required runtime is corrupt.
- [14] A stale instance lock remains.
- [15] Required OS permission is absent.
- [16] A worker crashes.
- [17] Startup is interrupted.
- [18] Shutdown is interrupted.

### Prevent a second Wisp instance from running at the same time.

- [19] The Wisp installation is missing.
- [20] The Wisp installation is corrupt.
- [21] The required runtime is missing.
- [22] The required runtime is corrupt.
- [23] A stale instance lock remains.
- [24] Required OS permission is absent.
- [25] A worker crashes.
- [26] Startup is interrupted.
- [27] Shutdown is interrupted.

### Run first-start setup and dependency checks.

- [28] The Wisp installation is missing.
- [29] The Wisp installation is corrupt.
- [x] [30] The required runtime is missing. [T002]
- [x] [31] The required runtime is corrupt. [T003]
- [32] A stale instance lock remains.
- [33] Required OS permission is absent.
- [34] A worker crashes.
- [35] Startup is interrupted.
- [36] Shutdown is interrupted.

### Show the guided profile/setup wizard again from Settings.

- [37] A value required by this function is invalid.
- [38] The settings store is read-only.
- [39] The settings store is corrupt.
- [40] A resource required by this function is missing.
- [41] The pending change is discarded before persistence.
- [42] The required application restart does not occur.

### Move backward and forward through setup steps.

- [43] A runtime required by this function is unavailable.
- [44] A permission required by this function is missing.
- [45] A required worker is unavailable.
- [46] The desktop tray facility is unavailable.
- [47] Saved startup state is unavailable.
- [48] Saved startup state is corrupt.

### Choose the app display language during setup.

- [49] The selected language value is invalid.
- [50] The settings store is read-only.
- [51] The settings store is corrupt.
- [52] A resource required by this function is missing.
- [53] The pending change is discarded before persistence.
- [54] The required application restart does not occur.

### Choose the assistant reply language during setup.

- [55] The selected language value is invalid.
- [56] The settings store is read-only.
- [57] The settings store is corrupt.
- [58] A resource required by this function is missing.
- [59] The pending change is discarded before persistence.
- [60] The required application restart does not occur.

### Choose System, Light, or Dark appearance during setup.

- [61] A value required by this function is invalid.
- [62] The settings store is read-only.
- [63] The settings store is corrupt.
- [64] A resource required by this function is missing.
- [65] The pending change is discarded before persistence.
- [66] The required application restart does not occur.

### Choose an AI provider, model, endpoint, and API key during setup.

- [67] A value required by this function is invalid.
- [68] The OS keychain is unavailable.
- [69] The endpoint URL is malformed.
- [70] The endpoint is offline.
- [71] The account lacks permission.
- [72] The setting cannot be saved.

### Sign in with a ChatGPT Plus/Pro account during setup.

- [73] The browser cannot open.
- [74] The authentication flow expires.
- [75] The authentication flow is cancelled.
- [76] The provider service is unavailable.
- [77] Network access is unavailable.
- [78] Requested scopes are rejected.
- [x] [79] The returned token cannot be stored. [T033]

### Choose no TTS, local TTS, or cloud TTS during setup.

- [80] A runtime required by this function is unavailable.
- [81] A permission required by this function is missing.
- [82] A required worker is unavailable.
- [83] The desktop tray facility is unavailable.
- [84] Saved startup state is unavailable.
- [85] Saved startup state is corrupt.

### Choose no STT, local Whisper, or cloud/live voice during setup.

- [86] A runtime required by this function is unavailable.
- [87] A permission required by this function is missing.
- [88] A required worker is unavailable.
- [89] The desktop tray facility is unavailable.
- [90] Saved startup state is unavailable.
- [91] Saved startup state is corrupt.

### Open a new chat automatically when setup finishes.

- [92] The conversation store is locked.
- [93] The conversation store is corrupt.
- [94] The selected conversation record is stale.
- [95] A stream is still active.
- [96] The configured model fails.
- [97] The configured tool fails.
- [98] Persistence is interrupted.

### Show first-use guidance for trying the main shortcut.

- [99] The key binding is invalid.
- [100] The key binding conflicts with another binding.
- [101] The OS rejects the global hook.
- [102] Input-monitoring permission is missing.
- [103] Accessibility permission is missing.
- [104] Another application consumes the event.

### Start the UI, brain/model, audio, and platform workers.

- [105] A required worker executable is missing.
- [106] A required worker dependency is missing.
- [107] Inter-process startup times out.
- [108] An old worker process remains.
- [109] A stale process lock remains.
- [110] Required OS permission is absent.
- [111] A worker crashes during initialization.

### Display a floating always-on-top Wisp icon.

- [112] The widget is hidden.
- [113] The widget was destroyed.
- [114] Another window consumes input.
- [115] Saved window geometry is stale.
- [116] The window is positioned off-screen.
- [117] The UI thread is blocked.
- [118] The window manager rejects the behavior.

### Show idle, listening, thinking, and speaking icon states.

- [119] A runtime required by this function is unavailable.
- [120] A permission required by this function is missing.
- [121] A required worker is unavailable.
- [122] The desktop tray facility is unavailable.
- [123] Saved startup state is unavailable.
- [124] Saved startup state is corrupt.

### Drag the floating icon to another screen position.

- [125] The widget is hidden.
- [126] The widget was destroyed.
- [127] Another window consumes input.
- [128] Saved window geometry is stale.
- [129] The window is positioned off-screen.
- [130] The UI thread is blocked.
- [131] The window manager rejects the behavior.

### Auto-hide the icon when Wisp is inactive.

- [132] The widget is hidden.
- [133] The widget was destroyed.
- [134] Another window consumes input.
- [135] Saved window geometry is stale.
- [136] The window is positioned off-screen.
- [137] The UI thread is blocked.
- [138] The window manager rejects the behavior.

### Show or hide the icon from the tray menu.

- [139] The widget is hidden.
- [140] The widget was destroyed.
- [141] Another window consumes input.
- [142] Saved window geometry is stale.
- [143] The window is positioned off-screen.
- [144] The UI thread is blocked.
- [145] The window manager rejects the behavior.

### Open the most recent chat from the tray menu.

- [146] The desktop lacks tray support.
- [147] The tray icon was not created.
- [148] The tray action callback was not created.
- [149] The UI target required by this function is unavailable.
- [150] The UI worker is unresponsive.

### Open Memory from the tray menu.

- [151] The desktop lacks tray support.
- [152] The tray icon was not created.
- [153] The tray action callback was not created.
- [154] The UI target required by this function is unavailable.
- [155] The UI worker is unresponsive.

### Open the Addon Manager from the tray menu.

- [156] The required component is disabled.
- [157] The required component is incompatible.
- [158] The component manifest is invalid.
- [159] The component permission configuration is invalid.
- [160] Dependencies are missing.
- [161] The isolated host crashes.
- [162] The MCP server is unavailable.
- [163] The MCP protocol is unavailable.

### Open Settings from the tray menu.

- [164] The desktop lacks tray support.
- [165] The tray icon was not created.
- [166] The tray action callback was not created.
- [167] The UI target required by this function is unavailable.
- [168] The UI worker is unresponsive.

### Open ChatGPT or Claude controls from the provider badge.

- [169] The desktop lacks tray support.
- [170] The tray icon was not created.
- [171] The tray action callback was not created.
- [172] The UI target required by this function is unavailable.
- [173] The UI worker is unresponsive.

### Open runtime status where the platform worker host supports it.

- [174] The Wisp installation is missing.
- [175] The Wisp installation is corrupt.
- [176] The required runtime is missing.
- [177] The required runtime is corrupt.
- [178] A stale instance lock remains.
- [179] Required OS permission is absent.
- [180] A worker crashes.
- [181] Startup is interrupted.
- [182] Shutdown is interrupted.

### Quit Wisp from the tray menu.

- [183] The desktop lacks tray support.
- [184] The tray icon was not created.
- [185] The tray action callback was not created.
- [186] The UI target required by this function is unavailable.
- [187] The UI worker is unresponsive.

### Start Wisp automatically when the user signs in.

- [188] The Wisp installation is missing.
- [189] The Wisp installation is corrupt.
- [190] The required runtime is missing.
- [191] The required runtime is corrupt.
- [192] A stale instance lock remains.
- [193] Required OS permission is absent.
- [194] A worker crashes.
- [195] Startup is interrupted.
- [196] Shutdown is interrupted.

### Shut workers down cleanly when Wisp exits.

- [197] The Wisp installation is missing.
- [198] The Wisp installation is corrupt.
- [199] The required runtime is missing.
- [200] The required runtime is corrupt.
- [201] A stale instance lock remains.
- [202] Required OS permission is absent.
- [x] [203] A worker crashes. [T005]
- [204] Startup is interrupted.
- [x] [205] Shutdown is interrupted. [T004]

### Capture the text currently selected in another application.

- [206] Required user input is empty.
- [207] Required context is empty.
- [208] The configured route fails.
- [209] The network request fails.
- [210] The request is cancelled.
- [211] The result cannot be rendered.
- [212] The result cannot be pasted into the target application.

### Open the general intent picker over the current application.

- [213] The source application is unsupported.
- [214] The source application is closed.
- [215] Accessibility permission is missing.
- [216] Automation permission is missing.
- [217] Extraction returns no text.
- [218] Content is truncated by its budget.

### Run the built-in What is this? action.

- [219] Required user input is empty.
- [220] Required context is empty.
- [221] The configured route fails.
- [222] The network request fails.
- [223] The request is cancelled.
- [224] The result cannot be rendered.
- [225] The result cannot be pasted into the target application.

### Run the built-in Explain simply action.

- [226] Required user input is empty.
- [227] Required context is empty.
- [228] The configured route fails.
- [229] The network request fails.
- [230] The request is cancelled.
- [231] The result cannot be rendered.
- [232] The result cannot be pasted into the target application.

### Run the built-in How do I fix this? action.

- [233] Required user input is empty.
- [234] Required context is empty.
- [235] The configured route fails.
- [236] The network request fails.
- [237] The request is cancelled.
- [238] The result cannot be rendered.
- [239] The result cannot be pasted into the target application.

### Open the rewrite/paste intent picker.

- [240] The clipboard is locked.
- [241] Focus changes before completion.
- [242] The target blocks synthetic input.
- [243] Another app overwrites the clipboard.
- [244] Accessibility permission is missing.

### Run the built-in Fix grammar action.

- [245] Required user input is empty.
- [246] Required context is empty.
- [x] [247] The configured route fails. [T029]
- [248] The network request fails.
- [249] The request is cancelled.
- [250] The result cannot be rendered.
- [251] The result cannot be pasted into the target application.

### Run the built-in Simplify action.

- [252] Required user input is empty.
- [253] Required context is empty.
- [254] The configured route fails.
- [255] The network request fails.
- [256] The request is cancelled.
- [257] The result cannot be rendered.
- [258] The result cannot be pasted into the target application.

### Run the built-in Improve tone action.

- [259] Required user input is empty.
- [260] Required context is empty.
- [261] The configured route fails.
- [262] The network request fails.
- [263] The request is cancelled.
- [264] The result cannot be rendered.
- [265] The result cannot be pasted into the target application.

### Type and submit a custom prompt from the intent picker.

- [266] Required user input is empty.
- [267] Required context is empty.
- [x] [268] The configured route fails. [T028]
- [269] The network request fails.
- [270] The request is cancelled.
- [271] The result cannot be rendered.
- [272] The result cannot be pasted into the target application.

### Choose an action with its assigned single-key shortcut.

- [273] The key binding is invalid.
- [274] The key binding conflicts with another binding.
- [275] The OS rejects the global hook.
- [276] Input-monitoring permission is missing.
- [277] Accessibility permission is missing.
- [278] Another application consumes the event.

### Choose an action by clicking it.

- [279] The widget is hidden.
- [280] The widget was destroyed.
- [281] Another window consumes input.
- [282] Saved window geometry is stale.
- [283] The window is positioned off-screen.
- [284] The UI thread is blocked.
- [285] The window manager rejects the behavior.

### Cancel an intent with Escape.

- [286] The key binding is invalid.
- [287] The key binding conflicts with another binding.
- [288] The OS rejects the global hook.
- [289] Input-monitoring permission is missing.
- [290] Accessibility permission is missing.
- [291] Another application consumes the event.

### Automatically close an abandoned intent picker after its configured timeout.

- [292] The configured duration is invalid.
- [293] The event loop is blocked.
- [294] A stale timer survives a state change.
- [295] Another modal window owns input.

### Keep the intent picker open until a choice is made by setting timeout to zero.

- [296] The configured duration is invalid.
- [297] The event loop is blocked.
- [298] A stale timer survives a state change.
- [299] Another modal window owns input.

### Paste a rewrite result back into the application that originally had focus.

- [300] The clipboard is locked.
- [301] Focus changes before completion.
- [302] The target blocks synthetic input.
- [303] Another app overwrites the clipboard.
- [304] Accessibility permission is missing.

### Keep an answer in Wisp without pasting it back.

- [305] Required user input is empty.
- [306] Required context is empty.
- [307] The configured route fails.
- [308] The network request fails.
- [309] The request is cancelled.
- [310] The result cannot be rendered.
- [311] The result cannot be pasted into the target application.

### Run localized built-in intent labels and prompts for supported assistant languages.

- [312] The selected language value is invalid.
- [313] The settings store is read-only.
- [314] The settings store is corrupt.
- [315] A resource required by this function is missing.
- [316] The pending change is discarded before persistence.
- [317] The required application restart does not occur.

### Stream the model response instead of waiting for the entire answer.

- [318] The route is incomplete.
- [319] Credentials are invalid.
- [320] The configured endpoint is unavailable.
- [321] The selected model is unavailable.
- [x] [322] The provider rate limit is reached. [T008]
- [323] The requested capability is unsupported.
- [x] [324] Every fallback fails. [T007]

### Route a request through the selected primary model and configured fallbacks.

- [325] The route is incomplete.
- [326] Credentials are invalid.
- [327] The configured endpoint is unavailable.
- [328] The selected model is unavailable.
- [x] [329] The provider rate limit is reached. [T008]
- [330] The requested capability is unsupported.
- [x] [331] Every fallback fails. [T007]

### Show a useful error/recovery recommendation when a request fails.

- [332] Required user input is empty.
- [333] Required context is empty.
- [x] [334] The configured route fails. [T028]
- [335] The network request fails.
- [336] The request is cancelled.
- [337] The result cannot be rendered.
- [338] The result cannot be pasted into the target application.

### Cancel an in-progress request.

- [339] The cancel event is not delivered.
- [340] The operation is blocked in an uninterruptible call.
- [341] Cleanup hangs.
- [342] The UI state is already stale.

### Use the default general picker shortcut: `Ctrl+Q` on Windows or `Ctrl+Alt+Space` on macOS/Linux.

- [x] [343] The key binding is invalid. [T013]
- [344] The key binding conflicts with another binding.
- [x] [345] The OS rejects the global hook. [T014]
- [346] Input-monitoring permission is missing.
- [347] Accessibility permission is missing.
- [348] Another application consumes the event.

### Use the default rewrite picker shortcut: `Ctrl+Shift+Q` on Windows or `Ctrl+Alt+Shift+Space` on macOS/Linux.

- [x] [349] The key binding is invalid. [T013]
- [350] The key binding conflicts with another binding.
- [x] [351] The OS rejects the global hook. [T014]
- [352] Input-monitoring permission is missing.
- [353] Accessibility permission is missing.
- [354] Another application consumes the event.

### Use the default screen snip shortcut: `Ctrl+Alt+Q`.

- [x] [355] The key binding is invalid. [T013]
- [356] The key binding conflicts with another binding.
- [x] [357] The OS rejects the global hook. [T014]
- [358] Input-monitoring permission is missing.
- [359] Accessibility permission is missing.
- [360] Another application consumes the event.

### Use the default add-selection-to-context shortcut: `Alt+Q`.

- [x] [361] The key binding is invalid. [T013]
- [362] The key binding conflicts with another binding.
- [x] [363] The OS rejects the global hook. [T014]
- [364] Input-monitoring permission is missing.
- [365] Accessibility permission is missing.
- [366] Another application consumes the event.

### Use the default clear-context shortcut: `Alt+W`.

- [x] [367] The key binding is invalid. [T013]
- [368] The key binding conflicts with another binding.
- [x] [369] The OS rejects the global hook. [T014]
- [370] Input-monitoring permission is missing.
- [371] Accessibility permission is missing.
- [372] Another application consumes the event.

### Use the default read-selection-aloud shortcut: `F7`.

- [x] [373] The key binding is invalid. [T013]
- [374] The key binding conflicts with another binding.
- [x] [375] The OS rejects the global hook. [T014]
- [376] Input-monitoring permission is missing.
- [377] Accessibility permission is missing.
- [378] Another application consumes the event.

### Hold the default voice-query shortcut `F9`, speak, and release to ask.

- [x] [379] The key binding is invalid. [T013]
- [380] The key binding conflicts with another binding.
- [x] [381] The OS rejects the global hook. [T014]
- [382] Input-monitoring permission is missing.
- [383] Accessibility permission is missing.
- [384] Another application consumes the event.

### Toggle live voice with `Shift+F9`.

- [x] [385] The key binding is invalid. [T013]
- [386] The key binding conflicts with another binding.
- [x] [387] The OS rejects the global hook. [T014]
- [388] Input-monitoring permission is missing.
- [389] Accessibility permission is missing.
- [390] Another application consumes the event.

### Hold the default dictation shortcut `F8`, speak, and release to paste.

- [x] [391] The key binding is invalid. [T013]
- [392] The key binding conflicts with another binding.
- [x] [393] The OS rejects the global hook. [T014]
- [394] Input-monitoring permission is missing.
- [395] Accessibility permission is missing.
- [396] Another application consumes the event.

### Search the shortcut list by action name or description.

- [397] The key binding is invalid.
- [398] The key binding conflicts with another binding.
- [399] The OS rejects the global hook.
- [400] Input-monitoring permission is missing.
- [401] Accessibility permission is missing.
- [402] Another application consumes the event.

### Enable or disable each shortcut independently.

- [403] The key binding is invalid.
- [404] The key binding conflicts with another binding.
- [405] The OS rejects the global hook.
- [406] Input-monitoring permission is missing.
- [407] Accessibility permission is missing.
- [408] Another application consumes the event.

### Click a shortcut field and record a replacement key combination.

- [x] [409] The key binding is invalid. [T013]
- [x] [410] The key binding conflicts with another binding. [T015]
- [411] The OS rejects the global hook.
- [412] Input-monitoring permission is missing.
- [413] Accessibility permission is missing.
- [414] Another application consumes the event.

### Clear or cancel a shortcut assignment.

- [415] The key binding is invalid.
- [416] The key binding conflicts with another binding.
- [417] The OS rejects the global hook.
- [418] Input-monitoring permission is missing.
- [419] Accessibility permission is missing.
- [420] Another application consumes the event.

### Assign two alternate shortcuts to the same action.

- [421] The key binding is invalid.
- [x] [422] The key binding conflicts with another binding. [T015]
- [423] The OS rejects the global hook.
- [424] Input-monitoring permission is missing.
- [425] Accessibility permission is missing.
- [426] Another application consumes the event.

### Detect and warn about conflicting shortcuts.

- [427] The key binding is invalid.
- [x] [428] The key binding conflicts with another binding. [T015]
- [429] The OS rejects the global hook.
- [430] Input-monitoring permission is missing.
- [431] Accessibility permission is missing.
- [432] Another application consumes the event.

### Add a new intent shortcut/caller.

- [433] The key binding is invalid.
- [434] The key binding conflicts with another binding.
- [435] The OS rejects the global hook.
- [436] Input-monitoring permission is missing.
- [437] Accessibility permission is missing.
- [438] Another application consumes the event.

### Rename an intent shortcut.

- [439] The new value is empty.
- [440] The new value is invalid.
- [441] The new value duplicates an existing value.
- [442] The backing store is read-only.
- [443] The backing store is locked.
- [444] The backing store is corrupt.
- [445] The write is interrupted.

### Remove an intent shortcut.

- [446] The key binding is invalid.
- [447] The key binding conflicts with another binding.
- [448] The OS rejects the global hook.
- [449] Input-monitoring permission is missing.
- [450] Accessibility permission is missing.
- [451] Another application consumes the event.

### Customize an intent shortcut's action choices.

- [452] The key binding is invalid.
- [453] The key binding conflicts with another binding.
- [454] The OS rejects the global hook.
- [455] Input-monitoring permission is missing.
- [456] Accessibility permission is missing.
- [457] Another application consumes the event.

### Add an action choice with its own key, label, and model prompt.

- [458] The key conflicts with another action.
- [459] Required fields are empty.
- [460] The prompt data is invalid.
- [461] The shortcut configuration cannot be persisted.

### Remove an action choice.

- [462] The OS rejects the key hook.
- [463] A binding conflicts.
- [464] Accessibility permission is missing.
- [465] Another app consumes input.

### Change an action choice's key, label, or model prompt.

- [466] The replacement key conflicts.
- [467] The edited value is invalid.
- [468] The row is stale.
- [469] The shortcut configuration store is read-only.
- [470] The shortcut configuration store is corrupt.

### Configure the custom-prompt action and its key.

- [471] The OS rejects the key hook.
- [472] A binding conflicts.
- [473] Accessibility permission is missing.
- [474] Another app consumes input.

### Enable or disable paste-back for an intent shortcut.

- [475] The key binding is invalid.
- [476] The key binding conflicts with another binding.
- [477] The OS rejects the global hook.
- [478] Input-monitoring permission is missing.
- [479] Accessibility permission is missing.
- [480] Another application consumes the event.

### Configure context sources separately for every intent shortcut.

- [481] The key binding is invalid.
- [482] The key binding conflicts with another binding.
- [483] The OS rejects the global hook.
- [484] Input-monitoring permission is missing.
- [485] Accessibility permission is missing.
- [486] Another application consumes the event.

### Configure allowed model tools separately for every intent shortcut.

- [487] The key binding is invalid.
- [488] The key binding conflicts with another binding.
- [489] The OS rejects the global hook.
- [490] Input-monitoring permission is missing.
- [491] Accessibility permission is missing.
- [492] Another application consumes the event.

### Configure context and allowed tools for voice queries.

- [493] The source is disabled.
- [494] The source is unavailable.
- [495] The source returned no content.
- [496] The source data is stale.
- [497] Capture permission is missing.
- [498] Saved policy is invalid.
- [499] Its content exceeds the configured budget.

### Configure context and allowed tools for screen-snip queries.

- [500] The source is disabled.
- [501] The source is unavailable.
- [502] The source returned no content.
- [503] The source data is stale.
- [504] Capture permission is missing.
- [505] Saved policy is invalid.
- [506] Its content exceeds the configured budget.

### Set dictation to raw transcript or LLM-cleaned transcript.

- [507] The OS rejects the key hook.
- [508] A binding conflicts.
- [509] Accessibility permission is missing.
- [510] Another app consumes input.

### Add selected text to a persistent context buffer.

- [511] Nothing is selected.
- [512] Focus moved.
- [513] The target control does not expose accessible text.
- [514] The OS permission is missing.
- [515] The target application is unsupported.
- [516] The platform backend is unsupported.

### Clear every item from the context buffer.

- [517] A target required by this function is missing.
- [518] A target required by this function is locked.
- [519] Confirmation is cancelled.
- [520] Required elevation is denied.
- [521] Storage access is denied.
- [522] Another process is using the files.
- [523] Cleanup only partly completes.

### Remove one context item without clearing the rest.

- [524] The source is disabled.
- [525] The source is unavailable.
- [526] The source returned no content.
- [527] The source data is stale.
- [528] Capture permission is missing.
- [529] Saved policy is invalid.
- [530] Its content exceeds the configured budget.

### Re-enable a context source that was removed or turned off.

- [531] The source is disabled.
- [532] The source is unavailable.
- [533] The source returned no content.
- [534] The source data is stale.
- [535] Capture permission is missing.
- [536] Saved policy is invalid.
- [537] Its content exceeds the configured budget.

### Paste clipboard items into the intent overlay as context.

- [538] The clipboard is locked.
- [539] Focus changes before completion.
- [540] The target blocks synthetic input.
- [541] Another app overwrites the clipboard.
- [542] Accessibility permission is missing.

### Drop files or images onto Wisp as context.

- [543] The image payload is missing.
- [544] The image is unreadable.
- [x] [545] The image exceeds the size limit. [T022]
- [546] The image encoding is invalid.
- [547] The route lacks image support.
- [548] The provider rejects the format.

### Preview enabled context before sending.

- [549] The source is disabled.
- [550] The source is unavailable.
- [551] The source returned no content.
- [552] The source data is stale.
- [553] Capture permission is missing.
- [554] Saved policy is invalid.
- [555] Its content exceeds the configured budget.

### Show context-source state and token estimates where available.

- [556] The source has not been fetched.
- [557] The tokenizer is unknown.
- [558] The model identity is unknown.
- [559] Capture fails.
- [560] The estimate becomes stale before the request is sent.

### Toggle context sources with the numbered overlay keys.

- [561] The key binding is invalid.
- [562] The key binding conflicts with another binding.
- [563] The OS rejects the global hook.
- [564] Input-monitoring permission is missing.
- [565] Accessibility permission is missing.
- [566] Another application consumes the event.

### Include nearby application/window/focused-control context.

- [567] Nothing is selected.
- [568] Focus moved.
- [569] The target control does not expose accessible text.
- [570] The OS permission is missing.
- [571] The target application is unsupported.
- [572] The platform backend is unsupported.

### Include supported open-document content.

- [573] The source application is unsupported.
- [574] The source application is closed.
- [575] Accessibility permission is missing.
- [576] Automation permission is missing.
- [577] Extraction returns no text.
- [578] Content is truncated by its budget.

### Include current clipboard text.

- [579] The clipboard is locked.
- [580] Focus changes before completion.
- [581] The target blocks synthetic input.
- [582] Another app overwrites the clipboard.
- [583] Accessibility permission is missing.

### Include current selected text.

- [584] Nothing is selected.
- [585] Focus moved.
- [586] The target control does not expose accessible text.
- [587] The OS permission is missing.
- [588] The target application is unsupported.
- [589] The platform backend is unsupported.

### Include the current browser page.

- [590] Network access is disabled.
- [591] The required tool is disabled.
- [592] The page blocks retrieval.
- [x] [593] No browser source is detected. [T073]
- [594] The response exceeds context limits.
- [595] The remote format changes.

### Search the web when the model needs current information.

- [596] Network access is disabled.
- [597] The required tool is disabled.
- [598] The page blocks retrieval.
- [599] No browser source is detected.
- [600] The response exceeds context limits.
- [601] The remote format changes.

### Retrieve a specific website/page for the model.

- [602] Network access is disabled.
- [603] The required tool is disabled.
- [604] The page blocks retrieval.
- [605] No browser source is detected.
- [606] The response exceeds context limits.
- [607] The remote format changes.

### Include local Git status and diff.

- [608] Git is unavailable.
- [609] The selected folder is not a repository.
- [610] The working tree cannot be read.
- [611] The scope is wrong.
- [612] The output budget is exceeded.

### Fetch GitHub repository metadata.

- [613] GitHub authentication is missing.
- [614] A required GitHub OAuth scope is missing.
- [615] The requested GitHub resource is private and inaccessible.
- [616] The requested GitHub resource does not exist.
- [617] Network access is unavailable.
- [618] The remote API is unavailable.
- [619] Its identifier is invalid.

### Fetch a GitHub issue or pull request by number.

- [620] GitHub authentication is missing.
- [621] A required GitHub OAuth scope is missing.
- [622] The requested GitHub resource is private and inaccessible.
- [623] The requested GitHub resource does not exist.
- [624] Network access is unavailable.
- [625] The remote API is unavailable.
- [626] Its identifier is invalid.

### Retrieve relevant long-term memory.

- [627] Memory is disabled.
- [628] The relevant data store is locked.
- [629] The relevant data store is corrupt.
- [630] The memory fact is rejected.
- [631] The memory fact duplicates an existing fact.
- [632] Project scope is wrong.
- [x] [633] Retrieval is empty. [T046]
- [634] The memory model route fails.

### Capture a screenshot immediately with the prompt.

- [635] Screen-recording permission is missing.
- [x] [636] The capture backend fails. [T026]
- [637] Monitor geometry changes during the operation.
- [638] DPI scaling changes during the operation.
- [639] The selected region is empty.
- [640] The target window disappears.

### Let the model request a screenshot only when needed.

- [641] Screen-recording permission is missing.
- [642] The capture backend fails.
- [643] Monitor geometry changes during the operation.
- [644] DPI scaling changes during the operation.
- [645] The selected region is empty.
- [646] The target window disappears.

### Let the model request open documents only when needed.

- [647] The source application is unsupported.
- [648] The source application is closed.
- [649] Accessibility permission is missing.
- [650] Automation permission is missing.
- [651] Extraction returns no text.
- [652] Content is truncated by its budget.

### Let the model request browser/web context only when needed.

- [653] Network access is disabled.
- [654] The required tool is disabled.
- [655] The page blocks retrieval.
- [656] No browser source is detected.
- [657] The response exceeds context limits.
- [658] The remote format changes.

### Let the model request Git/GitHub context only when needed.

- [659] GitHub authentication is missing.
- [660] A required GitHub OAuth scope is missing.
- [661] The requested GitHub resource is private and inaccessible.
- [662] The requested GitHub resource does not exist.
- [663] Network access is unavailable.
- [664] The remote API is unavailable.
- [665] Its identifier is invalid.

### Let the model search memory only when needed.

- [666] Memory is disabled.
- [667] The memory-search tool is disabled.
- [668] The model does not support tool calling.
- [669] The model does not issue the required tool call.
- [670] The memory store is unavailable.
- [x] [671] Retrieval returns nothing. [T046]
- [672] The tool budget is exhausted.

### Disable each context source for a particular shortcut or conversation.

- [673] The key binding is invalid.
- [674] The key binding conflicts with another binding.
- [675] The OS rejects the global hook.
- [676] Input-monitoring permission is missing.
- [677] Accessibility permission is missing.
- [678] Another application consumes the event.

### Set local file access to Off.

- [679] The path is outside configured allowed roots.
- [680] The path matches a blocked glob.
- [681] A target required by this function is missing.
- [682] A target required by this function is locked.
- [683] OS access is denied.
- [684] Approval is declined.
- [685] A concurrent change invalidates the operation.

### Set local file access to Read only.

- [686] The path is outside configured allowed roots.
- [687] The path matches a blocked glob.
- [688] A target required by this function is missing.
- [689] A target required by this function is locked.
- [690] OS access is denied.
- [691] Approval is declined.
- [692] A concurrent change invalidates the operation.

### Set local file access to Ask before writing.

- [693] The path is outside configured allowed roots.
- [694] The path matches a blocked glob.
- [695] A target required by this function is missing.
- [696] A target required by this function is locked.
- [697] OS access is denied.
- [698] Approval is declined.
- [699] A concurrent change invalidates the operation.

### Set local file access to Write automatically.

- [700] The path is outside configured allowed roots.
- [701] The path matches a blocked glob.
- [702] A target required by this function is missing.
- [703] A target required by this function is locked.
- [704] OS access is denied.
- [705] Approval is declined.
- [706] A concurrent change invalidates the operation.

### Limit file access to configured root folders.

- [707] The path is outside configured allowed roots.
- [708] The path matches a blocked glob.
- [709] A target required by this function is missing.
- [710] A target required by this function is locked.
- [711] OS access is denied.
- [712] Approval is declined.
- [713] A concurrent change invalidates the operation.

### Block private files with configurable glob patterns.

- [714] The path is outside configured allowed roots.
- [715] The path matches a blocked glob.
- [716] A target required by this function is missing.
- [717] A target required by this function is locked.
- [718] OS access is denied.
- [719] Approval is declined.
- [720] A concurrent change invalidates the operation.

### Limit browser, ambient-document, and tool-document context sizes.

- [721] The source is disabled.
- [722] The source is inaccessible.
- [723] The source returned no content.
- [724] The source data is stale.
- [x] [725] The source content exceeds the context budget. [T025]
- [726] The source content exceeds the tool-output budget.

### Open the full-screen snip overlay.

- [727] Screen-recording permission is missing.
- [728] The capture backend fails.
- [729] Monitor geometry changes during the operation.
- [730] DPI scaling changes during the operation.
- [731] The selected region is empty.
- [732] The target window disappears.

### Draw a rectangular screen region and attach it.

- [733] Screen-recording permission is missing.
- [734] The capture backend fails.
- [735] Monitor geometry changes during the operation.
- [736] DPI scaling changes during the operation.
- [x] [737] The selected region is empty. [T021]
- [738] The target window disappears.

### Capture the full screen.

- [739] Screen-recording permission is missing.
- [740] The capture backend fails.
- [741] Monitor geometry changes during the operation.
- [742] DPI scaling changes during the operation.
- [743] The selected region is empty.
- [744] The target window disappears.

### Capture the current application/window bounds.

- [745] Screen-recording permission is missing.
- [746] The capture backend fails.
- [747] Monitor geometry changes during the operation.
- [748] DPI scaling changes during the operation.
- [749] The selected region is empty.
- [750] The target window disappears.

### Switch between Area, App, and Full capture modes.

- [751] Screen-recording permission is missing.
- [752] The capture backend fails.
- [753] Monitor geometry changes during the operation.
- [754] DPI scaling changes during the operation.
- [755] The selected region is empty.
- [756] The target window disappears.

### Cancel capture with Escape.

- [757] The key binding is invalid.
- [758] The key binding conflicts with another binding.
- [759] The OS rejects the global hook.
- [760] Input-monitoring permission is missing.
- [761] Accessibility permission is missing.
- [762] Another application consumes the event.

### Attach the resulting image to the intent picker.

- [763] The source file is missing.
- [764] The source file is unreadable.
- [765] The source file exceeds the size limit.
- [766] The source file is blocked by policy.
- [767] The source file format is unsupported.
- [768] The source file is removed before submission.

### Ask a vision-capable model about the captured image.

- [769] The image payload is missing.
- [770] The image is unreadable.
- [x] [771] The image exceeds the size limit. [T024]
- [772] The image encoding is invalid.
- [x] [773] The route lacks image support. [T010]
- [774] The provider rejects the format.

### Route images through the configured Image model and its fallbacks.

- [775] The image payload is missing.
- [776] The image is unreadable.
- [x] [777] The image exceeds the size limit. [T024]
- [778] The image encoding is invalid.
- [x] [779] The route lacks image support. [T010]
- [780] The provider rejects the format.

### Show an image returned by a model in the reply bubble or chat.

- [781] The image payload is missing.
- [782] The image is unreadable.
- [783] The image exceeds the size limit.
- [784] The image encoding is invalid.
- [785] The route lacks image support.
- [786] The provider rejects the format.

### Show listening, transcript, progress, answer, warning, and error text beside the icon.

- [x] [787] Reply state is stale. [T027]
- [788] The UI thread is blocked.
- [789] The render data is invalid.
- [790] A timer callback fails.
- [791] A speech callback fails.

### Stream answer text into the bubble.

- [792] Reply state is stale.
- [793] The UI thread is blocked.
- [794] The render data is invalid.
- [795] A timer callback fails.
- [796] A speech callback fails.

### Reveal words progressively.

- [797] Reply state is stale.
- [798] The UI thread is blocked.
- [799] The render data is invalid.
- [800] A timer callback fails.
- [801] A speech callback fails.

### Highlight the currently spoken word when timestamps are available.

- [802] The optional runtime is missing.
- [803] A required model asset is missing.
- [804] A required model asset is damaged.
- [805] Microphone permission is denied.
- [806] Audio-output permission is denied.
- [807] The audio device is unavailable.
- [808] Provider authentication fails.
- [809] The provider network request fails.
- [810] The selected model is unsupported.
- [811] The selected device is unsupported.

### Use normal timed reveal when the TTS provider has no word timestamps.

- [812] Provider timestamp capability is detected incorrectly.
- [813] The reveal timing configuration is invalid.
- [814] The UI event loop is blocked.
- [815] Speech state ends without resetting the reveal timer.

### Hold the fast-forward control to speed text and speech.

- [816] Reply state is stale.
- [817] The UI thread is blocked.
- [818] The render data is invalid.
- [819] A timer callback fails.
- [820] A speech callback fails.

### Stop/cancel the current reply from the close/stop control.

- [821] The cancel event is not delivered.
- [822] The operation is blocked in an uninterruptible call.
- [823] Cleanup hangs.
- [824] The UI state is already stale.

### Dismiss a non-cancellable informational notice.

- [825] The cancel event is not delivered.
- [826] The operation is blocked in an uninterruptible call.
- [827] Cleanup hangs.
- [828] The UI state is already stale.

### Click the bubble to open the full chat.

- [829] The widget is hidden.
- [830] The widget was destroyed.
- [831] Another window consumes input.
- [832] Saved window geometry is stale.
- [833] The window is positioned off-screen.
- [834] The UI thread is blocked.
- [835] The window manager rejects the behavior.

### Drag the bubble/icon group.

- [836] Reply state is stale.
- [837] The UI thread is blocked.
- [838] The render data is invalid.
- [839] A timer callback fails.
- [840] A speech callback fails.

### Pause auto-hide while the user hovers or interacts.

- [841] The widget is hidden.
- [842] The widget was destroyed.
- [843] Another window consumes input.
- [844] Saved window geometry is stale.
- [845] The window is positioned off-screen.
- [846] The UI thread is blocked.
- [847] The window manager rejects the behavior.

### Wheel-scroll long bubble text.

- [848] The widget is hidden.
- [849] The widget was destroyed.
- [850] Another window consumes input.
- [851] Saved window geometry is stale.
- [852] The window is positioned off-screen.
- [853] The UI thread is blocked.
- [854] The window manager rejects the behavior.

### Snap manual scrolling back to the spoken/highlighted line.

- [855] Reply state is stale.
- [856] The UI thread is blocked.
- [857] The render data is invalid.
- [858] A timer callback fails.
- [859] A speech callback fails.

### Select text inside the bubble.

- [860] Reply state is stale.
- [861] The UI thread is blocked.
- [862] The render data is invalid.
- [863] A timer callback fails.
- [864] A speech callback fails.

### Copy selected bubble text.

- [865] The clipboard is locked.
- [866] Focus changes before completion.
- [867] The target blocks synthetic input.
- [868] Another app overwrites the clipboard.
- [869] Accessibility permission is missing.

### Copy the full bubble text.

- [870] Reply state is stale.
- [871] The UI thread is blocked.
- [872] The render data is invalid.
- [873] A timer callback fails.
- [874] A speech callback fails.

### Display assistant-created images.

- [875] The image payload is missing.
- [876] The image is unreadable.
- [877] The image exceeds the size limit.
- [878] The image encoding is invalid.
- [879] The route lacks image support.
- [880] The provider rejects the format.

### Edit or delete UI Lab labels from selected bubble text when that add-on is available.

- [881] The required component is disabled.
- [882] The required component is incompatible.
- [883] The component manifest is invalid.
- [884] The component permission configuration is invalid.
- [885] Dependencies are missing.
- [886] The isolated host crashes.
- [887] The MCP server is unavailable.
- [888] The MCP protocol is unavailable.

### Open persistent multi-turn chat.

- [889] The conversation store is locked.
- [x] [890] The conversation store is corrupt. [T030]
- [891] The selected conversation record is stale.
- [892] A stream is still active.
- [893] The configured model fails.
- [894] The configured tool fails.
- [895] Persistence is interrupted.

### Start a new chat from the button or `Ctrl+N`.

- [896] The key binding is invalid.
- [897] The key binding conflicts with another binding.
- [898] The OS rejects the global hook.
- [899] Input-monitoring permission is missing.
- [900] Accessibility permission is missing.
- [901] Another application consumes the event.

### Send with Enter and insert a newline with Shift+Enter.

- [902] The conversation store is locked.
- [903] The conversation store is corrupt.
- [904] The selected conversation record is stale.
- [905] A stream is still active.
- [906] The configured model fails.
- [907] The configured tool fails.
- [908] Persistence is interrupted.

### Stream assistant text, reasoning summaries, tool activity, and images.

- [909] The conversation store is locked.
- [910] The conversation store is corrupt.
- [911] The selected conversation record is stale.
- [912] A stream is still active.
- [913] The configured model fails.
- [914] The configured tool fails.
- [915] Persistence is interrupted.

### Continue a prior conversation.

- [916] The conversation store is locked.
- [917] The conversation store is corrupt.
- [918] The selected conversation record is stale.
- [919] A stream is still active.
- [920] The configured model fails.
- [921] The configured tool fails.
- [922] Persistence is interrupted.

### Choose whether an overlay request starts a new chat or continues an existing chat.

- [923] The conversation store is locked.
- [924] The conversation store is corrupt.
- [925] The selected conversation record is stale.
- [926] A stream is still active.
- [927] The configured model fails.
- [928] The configured tool fails.
- [929] Persistence is interrupted.

### Switch conversations from history.

- [930] The conversation store is locked.
- [931] The conversation store is corrupt.
- [932] The selected conversation record is stale.
- [933] A stream is still active.
- [934] The configured model fails.
- [935] The configured tool fails.
- [936] Persistence is interrupted.

### Group conversation history by project.

- [937] The conversation store is locked.
- [x] [938] The conversation store is corrupt. [T030]
- [939] The selected project record is stale.
- [940] A stream is still active.
- [941] The configured model fails.
- [942] The configured tool fails.
- [943] Persistence is interrupted.

### Create a project.

- [944] The new value is empty.
- [945] The new value is invalid.
- [946] The new value duplicates an existing value.
- [947] The backing store is read-only.
- [948] The backing store is locked.
- [x] [949] The backing store is corrupt. [T030]
- [950] The write is interrupted.

### Choose the project for new chats.

- [951] The conversation store is locked.
- [952] The conversation store is corrupt.
- [953] The selected project record is stale.
- [954] A stream is still active.
- [955] The configured model fails.
- [956] The configured tool fails.
- [957] Persistence is interrupted.

### Scope memory to the selected project.

- [958] The conversation store is locked.
- [959] The conversation store is corrupt.
- [960] The selected project record is stale.
- [961] A stream is still active.
- [962] The configured model fails.
- [963] The configured tool fails.
- [964] Persistence is interrupted.

### Add a conversation to a project.

- [965] The conversation store is locked.
- [966] The conversation store is corrupt.
- [967] The selected project record is stale.
- [968] A stream is still active.
- [969] The configured model fails.
- [970] The configured tool fails.
- [971] Persistence is interrupted.

### Pin or unpin a conversation.

- [972] The conversation store is locked.
- [973] The conversation store is corrupt.
- [974] The selected conversation record is stale.
- [975] A stream is still active.
- [976] The configured model fails.
- [977] The configured tool fails.
- [978] Persistence is interrupted.

### Rename a conversation.

- [979] The new value is empty.
- [980] The new value is invalid.
- [981] The new value duplicates an existing value.
- [982] The backing store is read-only.
- [983] The backing store is locked.
- [984] The backing store is corrupt.
- [985] The write is interrupted.

### Delete a conversation after confirmation.

- [986] A target required by this function is missing.
- [987] A target required by this function is locked.
- [988] Confirmation is cancelled.
- [989] Required elevation is denied.
- [990] Storage access is denied.
- [991] Another process is using the files.
- [992] Cleanup only partly completes.

### Browse files associated with a conversation.

- [993] The path is outside configured allowed roots.
- [994] The path matches a blocked glob.
- [995] A target required by this function is missing.
- [996] A target required by this function is locked.
- [997] OS access is denied.
- [998] Approval is declined.
- [999] A concurrent change invalidates the operation.

### Show conversation and message timestamps.

- [1000] The conversation store is locked.
- [1001] The conversation store is corrupt.
- [1002] The selected message record is stale.
- [1003] A stream is still active.
- [1004] The configured model fails.
- [1005] The configured tool fails.
- [1006] Persistence is interrupted.

### Attach one or more files or images with the file picker.

- [1007] The image payload is missing.
- [1008] The image is unreadable.
- [1009] The image exceeds the size limit.
- [1010] The image encoding is invalid.
- [1011] The route lacks image support.
- [1012] The provider rejects the format.

### Drag and drop files/images into chat.

- [1013] The source file is missing.
- [1014] The source file is unreadable.
- [x] [1015] The source file exceeds the size limit. [T022]
- [1016] The source file is blocked by policy.
- [1017] The source file format is unsupported.
- [1018] The source file is removed before submission.

### Show pending attachment names and context.

- [1019] The source file is missing.
- [1020] The source file is unreadable.
- [1021] The source file exceeds the size limit.
- [1022] The source file is blocked by policy.
- [1023] The source file format is unsupported.
- [1024] The source file is removed before submission.

### Display attached and returned image thumbnails.

- [1025] The image payload is missing.
- [1026] The image is unreadable.
- [x] [1027] The image exceeds the size limit. [T023]
- [1028] The image encoding is invalid.
- [1029] The route lacks image support.
- [1030] The provider rejects the format.

### Configure App context per conversation.

- [1031] The source is disabled.
- [1032] The source is unavailable.
- [1033] The source returned no content.
- [1034] The source data is stale.
- [1035] Capture permission is missing.
- [1036] Saved policy is invalid.
- [1037] Its content exceeds the configured budget.

### Configure Browser/Web context per conversation.

- [1038] Network access is disabled.
- [1039] The required tool is disabled.
- [1040] The page blocks retrieval.
- [1041] No browser source is detected.
- [1042] The response exceeds context limits.
- [1043] The remote format changes.

### Configure Selection context per conversation.

- [1044] The source is disabled.
- [1045] The source is unavailable.
- [1046] The source returned no content.
- [1047] The source data is stale.
- [1048] Capture permission is missing.
- [1049] Saved policy is invalid.
- [1050] Its content exceeds the configured budget.

### Configure Clipboard context per conversation.

- [1051] The clipboard is locked.
- [1052] Focus changes before completion.
- [1053] The target blocks synthetic input.
- [1054] Another app overwrites the clipboard.
- [1055] Accessibility permission is missing.

### Configure Screenshot context per conversation.

- [1056] Screen-recording permission is missing.
- [1057] The capture backend fails.
- [1058] Monitor geometry changes during the operation.
- [1059] DPI scaling changes during the operation.
- [1060] The selected region is empty.
- [1061] The target window disappears.

### Configure Git/GitHub context per conversation.

- [1062] GitHub authentication is missing.
- [1063] A required GitHub OAuth scope is missing.
- [1064] The requested GitHub resource is private and inaccessible.
- [1065] The requested GitHub resource does not exist.
- [1066] Network access is unavailable.
- [1067] The remote API is unavailable.
- [1068] Its identifier is invalid.

### Configure Memory context per conversation.

- [1069] The source is disabled.
- [1070] The source is unavailable.
- [1071] The source returned no content.
- [1072] The source data is stale.
- [1073] Capture permission is missing.
- [1074] Saved policy is invalid.
- [1075] Its content exceeds the configured budget.

### Configure Files access per conversation.

- [1076] The conversation store is locked.
- [1077] The conversation store is corrupt.
- [1078] The selected conversation record is stale.
- [1079] A stream is still active.
- [1080] The configured model fails.
- [1081] The configured tool fails.
- [1082] Persistence is interrupted.

### Capture selection or screenshot interactively when enabling its chat context chip.

- [1083] Screen-recording permission is missing.
- [1084] The capture backend fails.
- [1085] Monitor geometry changes during the operation.
- [1086] DPI scaling changes during the operation.
- [1087] The selected region is empty.
- [1088] The target window disappears.

### Preview context token estimates before sending.

- [1089] The source has not been fetched.
- [1090] The tokenizer is unknown.
- [1091] The model identity is unknown.
- [1092] Capture fails.
- [1093] The estimate becomes stale before the request is sent.

### Copy selected text from a message.

- [1094] Nothing is selected.
- [1095] Focus moved.
- [1096] The target control does not expose accessible text.
- [1097] The OS permission is missing.
- [1098] The target application is unsupported.
- [1099] The platform backend is unsupported.

### Branch a new conversation from any retained message.

- [1100] The conversation store is locked.
- [1101] The conversation store is corrupt.
- [1102] The selected message record is stale.
- [1103] A stream is still active.
- [1104] The configured model fails.
- [1105] The configured tool fails.
- [1106] Persistence is interrupted.

### Rewind the current conversation to any retained message.

- [1107] The conversation store is locked.
- [1108] The conversation store is corrupt.
- [1109] The selected message record is stale.
- [1110] A stream is still active.
- [1111] The configured model fails.
- [1112] The configured tool fails.
- [1113] Persistence is interrupted.

### Edit or delete UI Lab labels from selected chat text when that add-on is available.

- [1114] The required component is disabled.
- [1115] The required component is incompatible.
- [1116] The component manifest is invalid.
- [1117] The component permission configuration is invalid.
- [1118] Dependencies are missing.
- [1119] The isolated host crashes.
- [1120] The MCP server is unavailable.
- [1121] The MCP protocol is unavailable.

### Zoom chat text with the supported keyboard/wheel controls.

- [1122] The conversation store is locked.
- [1123] The conversation store is corrupt.
- [1124] The selected conversation record is stale.
- [1125] A stream is still active.
- [1126] The configured model fails.
- [1127] The configured tool fails.
- [1128] Persistence is interrupted.

### Show model tool-loop trace when enabled.

- [1129] The conversation store is locked.
- [1130] The conversation store is corrupt.
- [1131] The selected conversation record is stale.
- [1132] A stream is still active.
- [1133] The configured model fails.
- [1134] The configured tool fails.
- [1135] Persistence is interrupted.

### Split longer answers into planned chunks when enabled.

- [1136] The conversation store is locked.
- [1137] The conversation store is corrupt.
- [1138] The selected conversation record is stale.
- [1139] A stream is still active.
- [1140] The configured model fails.
- [1141] The configured tool fails.
- [1142] Persistence is interrupted.

### Set the number of planned chunks and the minimum prompt length.

- [1143] The conversation store is locked.
- [1144] The conversation store is corrupt.
- [1145] The selected conversation record is stale.
- [1146] A stream is still active.
- [1147] The configured model fails.
- [1148] The configured tool fails.
- [1149] Persistence is interrupted.

### Set chat reasoning effort.

- [1150] The conversation store is locked.
- [1151] The conversation store is corrupt.
- [1152] The selected conversation record is stale.
- [1153] A stream is still active.
- [1154] The configured model fails.
- [1155] The configured tool fails.
- [1156] Persistence is interrupted.

### Auto-elaborate the latest short answer when opening chat.

- [1157] The conversation store is locked.
- [1158] The conversation store is corrupt.
- [1159] The selected conversation record is stale.
- [1160] A stream is still active.
- [1161] The configured model fails.
- [1162] The configured tool fails.
- [1163] Persistence is interrupted.

### Customize the auto-elaboration prompt.

- [1164] The conversation store is locked.
- [1165] The conversation store is corrupt.
- [1166] The selected conversation record is stale.
- [1167] A stream is still active.
- [1168] The configured model fails.
- [1169] The configured tool fails.
- [1170] Persistence is interrupted.

### Pull local ChatGPT/Codex and Claude Code transcripts into Wisp.

- [1171] The external client is unavailable.
- [1172] The external session is unavailable.
- [1173] External authentication is invalid.
- [1174] The external workspace is invalid.
- [1175] The external transcript path changed.
- [1176] The external transcript format changed.
- [1177] Files are locked.
- [1178] Backup fails.
- [1179] The provider rejects the operation.

### Report imported, updated, and unchanged transcript counts.

- [1180] The external client is unavailable.
- [1181] The external session is unavailable.
- [1182] External authentication is invalid.
- [1183] The external workspace is invalid.
- [1184] The external transcript path changed.
- [1185] The external transcript format changed.
- [1186] Files are locked.
- [1187] Backup fails.
- [1188] The provider rejects the operation.

### Keep Wisp, ChatGPT, and Claude conversation namespaces distinct.

- [1189] The external client is unavailable.
- [1190] The external session is unavailable.
- [1191] External authentication is invalid.
- [1192] The external workspace is invalid.
- [1193] The external transcript path changed.
- [1194] The external transcript format changed.
- [1195] Files are locked.
- [1196] Backup fails.
- [1197] The provider rejects the operation.

### Continue a conversation using Wisp's own model engine.

- [1198] The external client is unavailable.
- [1199] The external session is unavailable.
- [1200] External authentication is invalid.
- [1201] The external workspace is invalid.
- [1202] The external transcript path changed.
- [1203] The external transcript format changed.
- [1204] Files are locked.
- [1205] Backup fails.
- [1206] The provider rejects the operation.

### Continue a conversation using the selected ChatGPT/Codex or Claude agent.

- [1207] The external client is unavailable.
- [1208] The external session is unavailable.
- [1209] External authentication is invalid.
- [1210] The external workspace is invalid.
- [1211] The external transcript path changed.
- [1212] The external transcript format changed.
- [1213] Files are locked.
- [1214] Backup fails.
- [1215] The provider rejects the operation.

### Choose whether continued messages belong to Wisp or the selected agent.

- [1216] The external client is unavailable.
- [1217] The external session is unavailable.
- [1218] External authentication is invalid.
- [1219] The external workspace is invalid.
- [1220] The external transcript path changed.
- [1221] The external transcript format changed.
- [1222] Files are locked.
- [1223] Backup fails.
- [1224] The provider rejects the operation.

### Push new Wisp turns back into their source transcript after confirmation.

- [1225] The external client is unavailable.
- [1226] The external session is unavailable.
- [1227] External authentication is invalid.
- [x] [1228] The external workspace is invalid. [T031]
- [1229] The external transcript path changed.
- [1230] The external transcript format changed.
- [1231] Files are locked.
- [1232] Backup fails.
- [1233] The provider rejects the operation.

### Create a backup before editing an external transcript.

- [1234] The external client is unavailable.
- [1235] The external session is unavailable.
- [1236] External authentication is invalid.
- [1237] The external workspace is invalid.
- [1238] The external transcript path changed.
- [1239] The external transcript format changed.
- [1240] Files are locked.
- [1241] Backup fails.
- [1242] The provider rejects the operation.

### Export a Wisp conversation as a new ChatGPT conversation.

- [1243] The external client is unavailable.
- [1244] The external session is unavailable.
- [1245] External authentication is invalid.
- [1246] The external workspace is invalid.
- [1247] The external transcript path changed.
- [1248] The external transcript format changed.
- [1249] Files are locked.
- [1250] Backup fails.
- [x] [1251] The provider rejects the operation. [T032]

### Export a Wisp conversation as a new Claude conversation.

- [1252] The external client is unavailable.
- [1253] The external session is unavailable.
- [1254] External authentication is invalid.
- [1255] The external workspace is invalid.
- [1256] The external transcript path changed.
- [1257] The external transcript format changed.
- [1258] Files are locked.
- [1259] Backup fails.
- [1260] The provider rejects the operation.

### Open provider controls from the floating provider badge.

- [1261] The external client is unavailable.
- [1262] The external session is unavailable.
- [1263] External authentication is invalid.
- [1264] The external workspace is invalid.
- [1265] The external transcript path changed.
- [1266] The external transcript format changed.
- [1267] Files are locked.
- [1268] Backup fails.
- [1269] The provider rejects the operation.

### Select provider-default or explicit agent model.

- [1270] The external client is unavailable.
- [1271] The external session is unavailable.
- [1272] External authentication is invalid.
- [1273] The external workspace is invalid.
- [1274] The external transcript path changed.
- [1275] The external transcript format changed.
- [1276] Files are locked.
- [1277] Backup fails.
- [1278] The provider rejects the operation.

### Choose or automatically detect the agent project/workspace folder.

- [1279] The external client is unavailable.
- [1280] The external session is unavailable.
- [1281] External authentication is invalid.
- [1282] The external workspace is invalid.
- [1283] The external transcript path changed.
- [1284] The external transcript format changed.
- [1285] Files are locked.
- [1286] Backup fails.
- [1287] The provider rejects the operation.

### Enable agent fast mode.

- [1288] The external client is unavailable.
- [1289] The external session is unavailable.
- [1290] External authentication is invalid.
- [1291] The external workspace is invalid.
- [1292] The external transcript path changed.
- [1293] The external transcript format changed.
- [1294] Files are locked.
- [1295] Backup fails.
- [1296] The provider rejects the operation.

### Choose agent reasoning effort: provider default, low, medium, high, xhigh, max, or ultra where supported.

- [1297] The external client is unavailable.
- [1298] The external session is unavailable.
- [1299] External authentication is invalid.
- [1300] The external workspace is invalid.
- [1301] The external transcript path changed.
- [1302] The external transcript format changed.
- [1303] Files are locked.
- [1304] Backup fails.
- [1305] The provider rejects the operation.

### Choose detailed, concise, provider, or no visible reasoning summaries.

- [1306] The external client is unavailable.
- [1307] The external session is unavailable.
- [1308] External authentication is invalid.
- [1309] The external workspace is invalid.
- [1310] The external transcript path changed.
- [1311] The external transcript format changed.
- [1312] Files are locked.
- [1313] Backup fails.
- [1314] The provider rejects the operation.

### Require approval for agent operations.

- [1315] The external client is unavailable.
- [1316] The external session is unavailable.
- [1317] External authentication is invalid.
- [1318] The external workspace is invalid.
- [1319] The external transcript path changed.
- [1320] The external transcript format changed.
- [1321] Files are locked.
- [1322] Backup fails.
- [1323] The provider rejects the operation.

### Allow agent edits within the selected project.

- [1324] The external client is unavailable.
- [1325] The external session is unavailable.
- [1326] External authentication is invalid.
- [1327] The external workspace is invalid.
- [1328] The external transcript path changed.
- [1329] The external transcript format changed.
- [1330] Files are locked.
- [1331] Backup fails.
- [1332] The provider rejects the operation.

### Grant full agent access.

- [1333] The external client is unavailable.
- [1334] The external session is unavailable.
- [1335] External authentication is invalid.
- [1336] The external workspace is invalid.
- [1337] The external transcript path changed.
- [1338] The external transcript format changed.
- [1339] Files are locked.
- [1340] Backup fails.
- [1341] The provider rejects the operation.

### Use plan-only/read-only agent mode.

- [1342] The external client is unavailable.
- [1343] The external session is unavailable.
- [1344] External authentication is invalid.
- [1345] The external workspace is invalid.
- [1346] The external transcript path changed.
- [1347] The external transcript format changed.
- [1348] Files are locked.
- [1349] Backup fails.
- [1350] The provider rejects the operation.

### Sign in to ChatGPT Plus/Pro in a browser.

- [1351] The browser cannot open.
- [1352] The authentication flow expires.
- [1353] The authentication flow is cancelled.
- [1354] The provider service is unavailable.
- [1355] Network access is unavailable.
- [1356] Requested scopes are rejected.
- [x] [1357] The returned token cannot be stored. [T033]

### Check ChatGPT sign-in status.

- [1358] The stored token is expired.
- [1359] The stored token is corrupt.
- [1360] The credential store cannot be read.
- [1361] The provider status endpoint is offline.
- [1362] The provider status request is rate-limited.

### Sign out of ChatGPT.

- [1363] The local credential store is locked.
- [1364] The local credential store is unavailable.
- [1365] The local token cannot be removed.
- [1366] Local and provider session state diverge.

### Sign in to GitHub with the device/browser OAuth flow.

- [1367] The browser cannot open.
- [1368] The authentication flow expires.
- [1369] The authentication flow is cancelled.
- [1370] The provider service is unavailable.
- [1371] Network access is unavailable.
- [1372] Requested scopes are rejected.
- [x] [1373] The returned token cannot be stored. [T034]

### Check GitHub sign-in status.

- [1374] The stored token is expired.
- [1375] The stored token is corrupt.
- [1376] The credential store cannot be read.
- [1377] The provider status endpoint is offline.
- [1378] The provider status request is rate-limited.

### Sign out of GitHub.

- [1379] The local credential store is locked.
- [1380] The local credential store is unavailable.
- [1381] The local token cannot be removed.
- [1382] Local and provider session state diverge.

### Override the GitHub OAuth client ID and scopes.

- [1383] The browser cannot open.
- [1384] The authentication flow expires.
- [1385] The authentication flow is cancelled.
- [1386] The provider service is unavailable.
- [1387] Network access is unavailable.
- [1388] Requested scopes are rejected.
- [1389] The returned token cannot be stored.

### Connect and clear GitHub Copilot credentials.

- [1390] The local credential store is locked.
- [1391] The local credential store is unavailable.
- [1392] The local token cannot be removed.
- [1393] Local and provider session state diverge.

### Test the GitHub Copilot connection.

- [1394] Credentials.
- [1395] Network access.
- [1396] Provider availability.
- [1397] The account lacks a required permission.
- [1398] The provider API is incompatible.

### Add a provider connection.

- [1399] A value required by this function is invalid.
- [1400] The OS keychain is unavailable.
- [1401] The endpoint URL is malformed.
- [1402] The endpoint is offline.
- [1403] The account lacks permission.
- [1404] The setting cannot be saved.

### Give a connection an alias.

- [1405] A value required by this function is invalid.
- [1406] The OS keychain is unavailable.
- [1407] The endpoint URL is malformed.
- [1408] The endpoint is offline.
- [1409] The account lacks permission.
- [1410] The setting cannot be saved.

### Store API keys in the operating-system keychain.

- [1411] A value required by this function is invalid.
- [1412] The OS keychain is unavailable.
- [1413] The endpoint URL is malformed.
- [1414] The endpoint is offline.
- [1415] The account lacks permission.
- [1416] The setting cannot be saved.

### Remove/clear a provider connection.

- [1417] A value required by this function is invalid.
- [1418] The OS keychain is unavailable.
- [1419] The endpoint URL is malformed.
- [1420] The endpoint is offline.
- [1421] The account lacks permission.
- [1422] The setting cannot be saved.

### Search connections by provider or alias.

- [1423] The route is incomplete.
- [1424] Credentials are invalid.
- [1425] The configured endpoint is unavailable.
- [1426] The selected model is unavailable.
- [1427] The provider rate limit is reached.
- [1428] The requested capability is unsupported.
- [1429] Every fallback fails.

### Filter All, Cloud, or Local/custom connections.

- [1430] Credentials.
- [1431] Network access.
- [1432] Provider availability.
- [1433] The account lacks a required permission.
- [1434] The provider API is incompatible.

### Expand or collapse large connection lists.

- [1435] Credentials.
- [1436] Network access.
- [1437] Provider availability.
- [1438] The account lacks a required permission.
- [1439] The provider API is incompatible.

### Configure a custom OpenAI-compatible base URL and API key.

- [1440] A value required by this function is invalid.
- [1441] The OS keychain is unavailable.
- [1442] The endpoint URL is malformed.
- [1443] The endpoint is offline.
- [1444] The account lacks permission.
- [1445] The setting cannot be saved.

### Pick a saved custom endpoint from the Endpoints menu.

- [1446] A value required by this function is invalid.
- [1447] The OS keychain is unavailable.
- [1448] The endpoint URL is malformed.
- [1449] The endpoint is offline.
- [1450] The account lacks permission.
- [1451] The setting cannot be saved.

### Use an existing local Ollama installation and auto-start its server when needed.

- [1452] The route is incomplete.
- [1453] Credentials are invalid.
- [1454] The configured endpoint is unavailable.
- [1455] The selected model is unavailable.
- [1456] The provider rate limit is reached.
- [1457] The requested capability is unsupported.
- [1458] Every fallback fails.

### Use an LM Studio or other OpenAI-compatible endpoint through Custom.

- [1459] The route is incomplete.
- [1460] Credentials are invalid.
- [1461] The configured endpoint is unavailable.
- [1462] The selected model is unavailable.
- [1463] The provider rate limit is reached.
- [1464] The requested capability is unsupported.
- [1465] Every fallback fails.

### Refresh model names from providers that support model listing.

- [1466] The provider does not support model listing.
- [1467] Authentication is missing.
- [1468] The provider API is offline.
- [1469] The provider API request is rate-limited.
- [1470] The response schema changed.

### Enter an exact model name manually.

- [1471] The model name is misspelled.
- [1472] The model is unavailable to the account.
- [1473] The model is unavailable at the endpoint.
- [1474] The model is paired with the wrong provider.
- [1475] The model is incompatible with the selected route.

### Groq.

- [1476] The required credential is unavailable.
- [1477] The required provider account is unavailable.
- [1478] The remote provider service is offline.
- [1479] The configured local endpoint is offline.
- [1480] The selected model is not accessible.
- [1481] Rate limits are reached.
- [1482] The provider API becomes incompatible.

### OpenAI API.

- [1483] The required credential is unavailable.
- [1484] The required provider account is unavailable.
- [1485] The remote provider service is offline.
- [1486] The configured local endpoint is offline.
- [1487] The selected model is not accessible.
- [1488] Rate limits are reached.
- [1489] The provider API becomes incompatible.

### Anthropic.

- [1490] The required credential is unavailable.
- [1491] The required provider account is unavailable.
- [1492] The remote provider service is offline.
- [1493] The configured local endpoint is offline.
- [1494] The selected model is not accessible.
- [1495] Rate limits are reached.
- [1496] The provider API becomes incompatible.

### Google AI Studio.

- [1497] The required credential is unavailable.
- [1498] The required provider account is unavailable.
- [1499] The remote provider service is offline.
- [1500] The configured local endpoint is offline.
- [1501] The selected model is not accessible.
- [1502] Rate limits are reached.
- [1503] The provider API becomes incompatible.

### ChatGPT Plus/Pro OAuth.

- [1504] The required credential is unavailable.
- [1505] The required provider account is unavailable.
- [1506] The remote provider service is offline.
- [1507] The configured local endpoint is offline.
- [1508] The selected model is not accessible.
- [1509] Rate limits are reached.
- [1510] The provider API becomes incompatible.

### GitHub Copilot.

- [1511] The required credential is unavailable.
- [1512] The required provider account is unavailable.
- [1513] The remote provider service is offline.
- [1514] The configured local endpoint is offline.
- [1515] The selected model is not accessible.
- [1516] Rate limits are reached.
- [1517] The provider API becomes incompatible.

### DeepSeek.

- [1518] The required credential is unavailable.
- [1519] The required provider account is unavailable.
- [1520] The remote provider service is offline.
- [1521] The configured local endpoint is offline.
- [1522] The selected model is not accessible.
- [1523] Rate limits are reached.
- [1524] The provider API becomes incompatible.

### OpenRouter.

- [x] [1525] The required credential is unavailable. [T011]
- [1526] The required provider account is unavailable.
- [1527] The remote provider service is offline.
- [1528] The configured local endpoint is offline.
- [1529] The selected model is not accessible.
- [1530] Rate limits are reached.
- [1531] The provider API becomes incompatible.

### Mistral.

- [1532] The required credential is unavailable.
- [1533] The required provider account is unavailable.
- [1534] The remote provider service is offline.
- [1535] The configured local endpoint is offline.
- [1536] The selected model is not accessible.
- [1537] Rate limits are reached.
- [1538] The provider API becomes incompatible.

### xAI/Grok.

- [1539] The required credential is unavailable.
- [1540] The required provider account is unavailable.
- [1541] The remote provider service is offline.
- [1542] The configured local endpoint is offline.
- [1543] The selected model is not accessible.
- [1544] Rate limits are reached.
- [1545] The provider API becomes incompatible.

### Together AI.

- [1546] The required credential is unavailable.
- [1547] The required provider account is unavailable.
- [1548] The remote provider service is offline.
- [1549] The configured local endpoint is offline.
- [1550] The selected model is not accessible.
- [1551] Rate limits are reached.
- [1552] The provider API becomes incompatible.

### Cerebras.

- [1553] The required credential is unavailable.
- [1554] The required provider account is unavailable.
- [1555] The remote provider service is offline.
- [1556] The configured local endpoint is offline.
- [1557] The selected model is not accessible.
- [1558] Rate limits are reached.
- [1559] The provider API becomes incompatible.

### Z.AI/GLM.

- [1560] The required credential is unavailable.
- [1561] The required provider account is unavailable.
- [1562] The remote provider service is offline.
- [1563] The configured local endpoint is offline.
- [1564] The selected model is not accessible.
- [1565] Rate limits are reached.
- [1566] The provider API becomes incompatible.

### NVIDIA.

- [1567] The required credential is unavailable.
- [1568] The required provider account is unavailable.
- [1569] The remote provider service is offline.
- [1570] The configured local endpoint is offline.
- [1571] The selected model is not accessible.
- [1572] Rate limits are reached.
- [1573] The provider API becomes incompatible.

### SambaNova.

- [1574] The required credential is unavailable.
- [1575] The required provider account is unavailable.
- [1576] The remote provider service is offline.
- [1577] The configured local endpoint is offline.
- [1578] The selected model is not accessible.
- [1579] Rate limits are reached.
- [1580] The provider API becomes incompatible.

### GitHub Models.

- [1581] The required credential is unavailable.
- [1582] The required provider account is unavailable.
- [1583] The remote provider service is offline.
- [1584] The configured local endpoint is offline.
- [1585] The selected model is not accessible.
- [1586] Rate limits are reached.
- [1587] The provider API becomes incompatible.

### Hugging Face.

- [1588] The required credential is unavailable.
- [1589] The required provider account is unavailable.
- [1590] The remote provider service is offline.
- [1591] The configured local endpoint is offline.
- [1592] The selected model is not accessible.
- [1593] Rate limits are reached.
- [1594] The provider API becomes incompatible.

### Chutes.

- [1595] The required credential is unavailable.
- [1596] The required provider account is unavailable.
- [1597] The remote provider service is offline.
- [1598] The configured local endpoint is offline.
- [1599] The selected model is not accessible.
- [1600] Rate limits are reached.
- [1601] The provider API becomes incompatible.

### Vercel AI Gateway.

- [1602] The required credential is unavailable.
- [1603] The required provider account is unavailable.
- [1604] The remote provider service is offline.
- [1605] The configured local endpoint is offline.
- [1606] The selected model is not accessible.
- [1607] Rate limits are reached.
- [1608] The provider API becomes incompatible.

### Fireworks.

- [1609] The required credential is unavailable.
- [1610] The required provider account is unavailable.
- [1611] The remote provider service is offline.
- [1612] The configured local endpoint is offline.
- [1613] The selected model is not accessible.
- [1614] Rate limits are reached.
- [1615] The provider API becomes incompatible.

### Cohere.

- [1616] The required credential is unavailable.
- [1617] The required provider account is unavailable.
- [1618] The remote provider service is offline.
- [1619] The configured local endpoint is offline.
- [1620] The selected model is not accessible.
- [1621] Rate limits are reached.
- [1622] The provider API becomes incompatible.

### AI21.

- [1623] The required credential is unavailable.
- [1624] The required provider account is unavailable.
- [1625] The remote provider service is offline.
- [1626] The configured local endpoint is offline.
- [1627] The selected model is not accessible.
- [1628] Rate limits are reached.
- [1629] The provider API becomes incompatible.

### Nebius.

- [1630] The required credential is unavailable.
- [1631] The required provider account is unavailable.
- [1632] The remote provider service is offline.
- [1633] The configured local endpoint is offline.
- [1634] The selected model is not accessible.
- [1635] Rate limits are reached.
- [1636] The provider API becomes incompatible.

### Ollama local.

- [x] [1637] The Ollama executable is not installed. [T012]
- [x] [1638] The Ollama executable cannot be found. [T012]
- [1639] The Ollama server is not running.
- [1640] The Ollama server cannot be started.
- [1641] The requested Ollama model is not installed.
- [1642] The configured Ollama host is wrong.
- [1643] The configured Ollama port is wrong.
- [1644] Available RAM is insufficient for the model.
- [1645] Available VRAM is insufficient for the model.

### Custom OpenAI-compatible provider.

- [1646] The custom base URL is invalid.
- [1647] The custom endpoint is offline.
- [1648] The custom endpoint rejects its credential.
- [1649] The requested model is not hosted by the endpoint.
- [1650] The endpoint is not sufficiently OpenAI-compatible.

### Choose the primary provider/model for the Chat model route.

- [1651] The conversation store is locked.
- [1652] The conversation store is corrupt.
- [1653] The selected conversation record is stale.
- [1654] A stream is still active.
- [1655] The configured model fails.
- [1656] The configured tool fails.
- [1657] Persistence is interrupted.

### Add, remove, and reorder Chat model fallbacks.

- [1658] The conversation store is locked.
- [1659] The conversation store is corrupt.
- [1660] The selected conversation record is stale.
- [1661] A stream is still active.
- [1662] The configured model fails.
- [1663] The configured tool fails.
- [1664] Persistence is interrupted.

### Test Chat model from Settings.

- [x] [1665] The route is incomplete. [T006]
- [x] [1666] Authentication is invalid. [T011]
- [1667] The configured endpoint is unavailable.
- [1668] Network access is unavailable.
- [1669] The selected model is missing.
- [1670] The provider rate limit is reached.
- [1671] The tested capability is unsupported.

### Choose the primary provider/model for the Image model route.

- [1672] The route is incomplete.
- [1673] Credentials are invalid.
- [1674] The configured endpoint is unavailable.
- [1675] The selected model is unavailable.
- [1676] The provider rate limit is reached.
- [1677] The requested capability is unsupported.
- [1678] Every fallback fails.

### Add, remove, and reorder Image model fallbacks.

- [1679] The route is incomplete.
- [1680] Credentials are invalid.
- [1681] The configured endpoint is unavailable.
- [1682] The selected model is unavailable.
- [1683] The provider rate limit is reached.
- [1684] The requested capability is unsupported.
- [1685] Every fallback fails.

### Test Image model from Settings.

- [x] [1686] The route is incomplete. [T006]
- [1687] Authentication is invalid.
- [1688] The configured endpoint is unavailable.
- [1689] Network access is unavailable.
- [1690] The selected model is missing.
- [1691] The provider rate limit is reached.
- [1692] The tested capability is unsupported.

### Choose the primary provider/model for the Memory model route.

- [1693] The route is incomplete.
- [1694] Credentials are invalid.
- [1695] The configured endpoint is unavailable.
- [1696] The selected model is unavailable.
- [1697] The provider rate limit is reached.
- [1698] The requested capability is unsupported.
- [1699] Every fallback fails.

### Add, remove, and reorder Memory model fallbacks.

- [1700] The route is incomplete.
- [1701] Credentials are invalid.
- [1702] The configured endpoint is unavailable.
- [1703] The selected model is unavailable.
- [1704] The provider rate limit is reached.
- [1705] The requested capability is unsupported.
- [1706] Every fallback fails.

### Test Memory model from Settings.

- [x] [1707] The route is incomplete. [T006]
- [1708] Authentication is invalid.
- [1709] The configured endpoint is unavailable.
- [1710] Network access is unavailable.
- [1711] The selected model is missing.
- [1712] The provider rate limit is reached.
- [1713] The tested capability is unsupported.

### Copy one route's provider/model rows to all model routes with Apply to all.

- [1714] The route is incomplete.
- [1715] Credentials are invalid.
- [1716] The configured endpoint is unavailable.
- [1717] The selected model is unavailable.
- [1718] The provider rate limit is reached.
- [1719] The requested capability is unsupported.
- [1720] Every fallback fails.

### Drag model rows to change priority.

- [1721] The widget is hidden.
- [1722] The widget was destroyed.
- [1723] Another window consumes input.
- [1724] Saved window geometry is stale.
- [1725] The window is positioned off-screen.
- [1726] The UI thread is blocked.
- [1727] The window manager rejects the behavior.

### Refresh available model IDs for an individual route row.

- [1728] The provider does not support model listing.
- [1729] Authentication is missing.
- [1730] The provider API is offline.
- [1731] The provider API request is rate-limited.
- [1732] The response schema changed.

### Fall through to the next configured model when a route fails.

- [1733] The route is incomplete.
- [1734] Credentials are invalid.
- [1735] The configured endpoint is unavailable.
- [1736] The selected model is unavailable.
- [x] [1737] The provider rate limit is reached. [T008]
- [1738] The requested capability is unsupported.
- [x] [1739] Every fallback fails. [T007]

### Temporarily cool down a failing route before retrying it later.

- [1740] The route is incomplete.
- [1741] Credentials are invalid.
- [1742] The configured endpoint is unavailable.
- [1743] The selected model is unavailable.
- [x] [1744] The provider rate limit is reached. [T008]
- [1745] The requested capability is unsupported.
- [x] [1746] Every fallback fails. [T007]

### Adapt to provider differences such as streaming, tools, images, token parameters, and reasoning controls.

- [1747] The route is incomplete.
- [1748] Credentials are invalid.
- [1749] The configured endpoint is unavailable.
- [1750] The selected model is unavailable.
- [1751] The provider rate limit is reached.
- [x] [1752] The requested capability is unsupported. [T009]
- [1753] Every fallback fails.

### Show warnings when a chosen model/provider cannot satisfy an enabled capability.

- [1754] The route is incomplete.
- [1755] Credentials are invalid.
- [1756] The configured endpoint is unavailable.
- [1757] The selected model is unavailable.
- [1758] The provider rate limit is reached.
- [x] [1759] The requested capability is unsupported. [T010]
- [1760] Every fallback fails.

### Use Wisp, ChatGPT, or Claude Agent as the conversation execution engine.

- [1761] The conversation store is locked.
- [1762] The conversation store is corrupt.
- [1763] The selected conversation record is stale.
- [1764] A stream is still active.
- [1765] The configured model fails.
- [1766] The configured tool fails.
- [1767] Persistence is interrupted.

### Choose Wisp or the selected agent as conversation owner.

- [1768] The conversation store is locked.
- [1769] The conversation store is corrupt.
- [1770] The selected conversation record is stale.
- [1771] A stream is still active.
- [1772] The configured model fails.
- [1773] The configured tool fails.
- [1774] Persistence is interrupted.

### Edit separate system prompts for Wisp, ChatGPT, and Claude conversations.

- [1775] The conversation store is locked.
- [1776] The conversation store is corrupt.
- [1777] The selected conversation record is stale.
- [1778] A stream is still active.
- [1779] The configured model fails.
- [1780] The configured tool fails.
- [1781] Persistence is interrupted.

### Disable text to speech while retaining manual read-aloud/test features.

- [1782] The optional runtime is missing.
- [1783] A required model asset is missing.
- [1784] A required model asset is damaged.
- [1785] Microphone permission is denied.
- [1786] Audio-output permission is denied.
- [1787] The audio device is unavailable.
- [1788] Provider authentication fails.
- [1789] The provider network request fails.
- [1790] The selected model is unsupported.
- [1791] The selected device is unsupported.

### Automatically speak assistant replies.

- [1792] The optional runtime is missing.
- [1793] A required model asset is missing.
- [1794] A required model asset is damaged.
- [1795] Microphone permission is denied.
- [1796] Audio-output permission is denied.
- [1797] The audio device is unavailable.
- [1798] Provider authentication fails.
- [1799] The provider network request fails.
- [1800] The selected model is unsupported.
- [1801] The selected device is unsupported.

### Read selected text aloud on demand.

- [x] [1802] Nothing is selected. [T068]
- [1803] Focus moved.
- [1804] The target control does not expose accessible text.
- [1805] The OS permission is missing.
- [1806] The target application is unsupported.
- [1807] The platform backend is unsupported.

### Stop current speech playback.

- [1808] The optional runtime is missing.
- [1809] A required model asset is missing.
- [1810] A required model asset is damaged.
- [1811] Microphone permission is denied.
- [1812] Audio-output permission is denied.
- [1813] The audio device is unavailable.
- [1814] Provider authentication fails.
- [1815] The provider network request fails.
- [1816] The selected model is unsupported.
- [1817] The selected device is unsupported.

### Set global TTS playback volume.

- [1818] The optional runtime is missing.
- [1819] A required model asset is missing.
- [1820] A required model asset is damaged.
- [1821] Microphone permission is denied.
- [1822] Audio-output permission is denied.
- [1823] The audio device is unavailable.
- [1824] Provider authentication fails.
- [1825] The provider network request fails.
- [1826] The selected model is unsupported.
- [1827] The selected device is unsupported.

### Set normal and held/fast-forward TTS playback speed.

- [1828] The optional runtime is missing.
- [1829] A required model asset is missing.
- [1830] A required model asset is damaged.
- [1831] Microphone permission is denied.
- [1832] Audio-output permission is denied.
- [1833] The audio device is unavailable.
- [1834] Provider authentication fails.
- [1835] The provider network request fails.
- [1836] The selected model is unsupported.
- [1837] The selected device is unsupported.

### Configure read-aloud minimum and maximum chunk size.

- [1838] The optional runtime is missing.
- [1839] A required model asset is missing.
- [1840] A required model asset is damaged.
- [1841] Microphone permission is denied.
- [1842] Audio-output permission is denied.
- [1843] The audio device is unavailable.
- [1844] Provider authentication fails.
- [1845] The provider network request fails.
- [1846] The selected model is unsupported.
- [1847] The selected device is unsupported.

### Test the selected TTS provider with Test TTS.

- [x] [1848] The route is incomplete. [T035]
- [1849] Authentication is invalid.
- [1850] The configured endpoint is unavailable.
- [1851] Network access is unavailable.
- [1852] The selected model is missing.
- [1853] The provider rate limit is reached.
- [1854] The tested capability is unsupported.

### Use Cartesia TTS and configure its key and voice ID.

- [1855] The optional runtime is missing.
- [1856] A required model asset is missing.
- [1857] A required model asset is damaged.
- [1858] Microphone permission is denied.
- [1859] Audio-output permission is denied.
- [1860] The audio device is unavailable.
- [1861] Provider authentication fails.
- [1862] The provider network request fails.
- [1863] The selected model is unsupported.
- [1864] The selected device is unsupported.

### Install and use ElevenLabs TTS; configure key, voice, and model.

- [1865] Network access is unavailable.
- [1866] The package source is unavailable.
- [1867] Available disk space is insufficient.
- [1868] Filesystem permission is insufficient.
- [1869] Verification fails.
- [1870] Dependency versions conflict.
- [1871] The operation is cancelled.

### Use OpenAI TTS; configure voice and model.

- [1872] The optional runtime is missing.
- [1873] A required model asset is missing.
- [1874] A required model asset is damaged.
- [1875] Microphone permission is denied.
- [1876] Audio-output permission is denied.
- [1877] The audio device is unavailable.
- [1878] Provider authentication fails.
- [1879] The provider network request fails.
- [1880] The selected model is unsupported.
- [1881] The selected device is unsupported.

### Use an OpenAI-compatible `/audio/speech` endpoint; configure URL, key, voice, model, and sample rate.

- [1882] The optional runtime is missing.
- [1883] A required model asset is missing.
- [1884] A required model asset is damaged.
- [1885] Microphone permission is denied.
- [1886] Audio-output permission is denied.
- [1887] The audio device is unavailable.
- [1888] Provider authentication fails.
- [1889] The provider network request fails.
- [1890] The selected model is unsupported.
- [1891] The selected device is unsupported.

### Use a local GPT-SoVITS server; configure reference audio/transcript/languages and sample rate.

- [1892] The optional runtime is missing.
- [1893] A required model asset is missing.
- [1894] A required model asset is damaged.
- [1895] Microphone permission is denied.
- [1896] Audio-output permission is denied.
- [1897] The audio device is unavailable.
- [1898] Provider authentication fails.
- [1899] The provider network request fails.
- [1900] The selected model is unsupported.
- [1901] The selected device is unsupported.

### Install and use local Kokoro TTS.

- [1902] Network access is unavailable.
- [1903] The package source is unavailable.
- [x] [1904] Available disk space is insufficient. [T038]
- [1905] Filesystem permission is insufficient.
- [1906] Verification fails.
- [1907] Dependency versions conflict.
- [1908] The operation is cancelled.

### Configure Kokoro voice, language code, device, speed, and sample rate.

- [1909] The optional runtime is missing.
- [x] [1910] A required model asset is missing. [T037]
- [x] [1911] A required model asset is damaged. [T036]
- [1912] Microphone permission is denied.
- [1913] Audio-output permission is denied.
- [1914] The audio device is unavailable.
- [1915] Provider authentication fails.
- [1916] The provider network request fails.
- [1917] The selected model is unsupported.
- [1918] The selected device is unsupported.

### Download, repair, or update Kokoro voice-model assets.

- [1919] Network access is unavailable.
- [1920] The package source is unavailable.
- [1921] Available disk space is insufficient.
- [1922] Filesystem permission is insufficient.
- [x] [1923] Verification fails. [T036]
- [1924] Dependency versions conflict.
- [1925] The operation is cancelled.

### Choose automatic, CPU, or CUDA speech device where supported.

- [1926] The optional runtime is missing.
- [1927] A required model asset is missing.
- [1928] A required model asset is damaged.
- [1929] Microphone permission is denied.
- [1930] Audio-output permission is denied.
- [1931] The audio device is unavailable.
- [1932] Provider authentication fails.
- [1933] The provider network request fails.
- [1934] The selected model is unsupported.
- [1935] The selected device is unsupported.

### Install local faster-whisper speech-to-text.

- [1936] Network access is unavailable.
- [1937] The package source is unavailable.
- [1938] Available disk space is insufficient.
- [1939] Filesystem permission is insufficient.
- [x] [1940] Verification fails. [T039]
- [1941] Dependency versions conflict.
- [1942] The operation is cancelled.

### Choose Whisper model size.

- [1943] The optional runtime is missing.
- [1944] A required model asset is missing.
- [1945] A required model asset is damaged.
- [1946] Microphone permission is denied.
- [1947] Audio-output permission is denied.
- [1948] The audio device is unavailable.
- [1949] Provider authentication fails.
- [1950] The provider network request fails.
- [1951] The selected model is unsupported.
- [1952] The selected device is unsupported.

### Choose STT device and compute type.

- [1953] The optional runtime is missing.
- [1954] A required model asset is missing.
- [1955] A required model asset is damaged.
- [1956] Microphone permission is denied.
- [1957] Audio-output permission is denied.
- [1958] The audio device is unavailable.
- [1959] Provider authentication fails.
- [1960] The provider network request fails.
- [1961] The selected model is unsupported.
- [x] [1962] The selected device is unsupported. [T040]

### Choose automatic or explicit speech language.

- [1963] The optional runtime is missing.
- [1964] A required model asset is missing.
- [1965] A required model asset is damaged.
- [1966] Microphone permission is denied.
- [1967] Audio-output permission is denied.
- [1968] The audio device is unavailable.
- [1969] Provider authentication fails.
- [1970] The provider network request fails.
- [1971] The selected model is unsupported.
- [1972] The selected device is unsupported.

### Configure Whisper beam size.

- [1973] The optional runtime is missing.
- [1974] A required model asset is missing.
- [1975] A required model asset is damaged.
- [1976] Microphone permission is denied.
- [1977] Audio-output permission is denied.
- [1978] The audio device is unavailable.
- [1979] Provider authentication fails.
- [1980] The provider network request fails.
- [1981] The selected model is unsupported.
- [1982] The selected device is unsupported.

### Transcribe long recordings in overlapping background chunks.

- [1983] The optional runtime is missing.
- [1984] A required model asset is missing.
- [1985] A required model asset is damaged.
- [1986] Microphone permission is denied.
- [1987] Audio-output permission is denied.
- [1988] The audio device is unavailable.
- [1989] Provider authentication fails.
- [1990] The provider network request fails.
- [1991] The selected model is unsupported.
- [1992] The selected device is unsupported.

### Configure first STT chunk time, cadence, live-edge delay, and overlap.

- [1993] The optional runtime is missing.
- [1994] A required model asset is missing.
- [1995] A required model asset is damaged.
- [1996] Microphone permission is denied.
- [1997] Audio-output permission is denied.
- [1998] The audio device is unavailable.
- [1999] Provider authentication fails.
- [2000] The provider network request fails.
- [2001] The selected model is unsupported.
- [2002] The selected device is unsupported.

### Review a voice transcript and its context before asking.

- [2003] The optional runtime is missing.
- [2004] A required model asset is missing.
- [2005] A required model asset is damaged.
- [2006] Microphone permission is denied.
- [2007] Audio-output permission is denied.
- [x] [2008] The audio device is unavailable. [T041][T042]
- [2009] Provider authentication fails.
- [2010] The provider network request fails.
- [2011] The selected model is unsupported.
- [2012] The selected device is unsupported.

### Send a voice transcript directly without review.

- [2013] The optional runtime is missing.
- [2014] A required model asset is missing.
- [2015] A required model asset is damaged.
- [2016] Microphone permission is denied.
- [2017] Audio-output permission is denied.
- [x] [2018] The audio device is unavailable. [T041][T042]
- [2019] Provider authentication fails.
- [2020] The provider network request fails.
- [2021] The selected model is unsupported.
- [2022] The selected device is unsupported.

### Dictate raw speech into the currently focused field.

- [2023] The clipboard is locked.
- [2024] Focus changes before completion.
- [2025] The target blocks synthetic input.
- [2026] Another app overwrites the clipboard.
- [2027] Accessibility permission is missing.

### Clean dictated speech with the LLM before pasting it.

- [2028] The optional runtime is missing.
- [2029] A required model asset is missing.
- [2030] A required model asset is damaged.
- [2031] Microphone permission is denied.
- [2032] Audio-output permission is denied.
- [x] [2033] The audio device is unavailable. [T041][T043]
- [2034] Provider authentication fails.
- [2035] The provider network request fails.
- [2036] The selected model is unsupported.
- [2037] The selected device is unsupported.

### Install live-voice support.

- [2038] Network access is unavailable.
- [2039] The package source is unavailable.
- [2040] Available disk space is insufficient.
- [2041] Filesystem permission is insufficient.
- [2042] Verification fails.
- [2043] Dependency versions conflict.
- [2044] The operation is cancelled.

### Start and stop a hands-free Gemini Live conversation.

- [2045] The optional runtime is missing.
- [2046] A required model asset is missing.
- [2047] A required model asset is damaged.
- [2048] Microphone permission is denied.
- [2049] Audio-output permission is denied.
- [2050] The audio device is unavailable.
- [x] [2051] Provider authentication fails. [T044]
- [2052] The provider network request fails.
- [2053] The selected model is unsupported.
- [2054] The selected device is unsupported.

### Display live user and assistant transcripts.

- [2055] The optional runtime is missing.
- [2056] A required model asset is missing.
- [2057] A required model asset is damaged.
- [2058] Microphone permission is denied.
- [2059] Audio-output permission is denied.
- [2060] The audio device is unavailable.
- [2061] Provider authentication fails.
- [2062] The provider network request fails.
- [2063] The selected model is unsupported.
- [2064] The selected device is unsupported.

### Interrupt Wisp by speaking over it when full duplex is enabled.

- [2065] The optional runtime is missing.
- [2066] A required model asset is missing.
- [2067] A required model asset is damaged.
- [2068] Microphone permission is denied.
- [2069] Audio-output permission is denied.
- [2070] The audio device is unavailable.
- [2071] Provider authentication fails.
- [2072] The provider network request fails.
- [2073] The selected model is unsupported.
- [2074] The selected device is unsupported.

### Pause the microphone while Wisp speaks in speaker/half-duplex mode.

- [2075] The optional runtime is missing.
- [2076] A required model asset is missing.
- [2077] A required model asset is damaged.
- [2078] Microphone permission is denied.
- [2079] Audio-output permission is denied.
- [2080] The audio device is unavailable.
- [2081] Provider authentication fails.
- [2082] The provider network request fails.
- [2083] The selected model is unsupported.
- [2084] The selected device is unsupported.

### Choose the live-conversation provider, model, and voice.

- [2085] The optional runtime is missing.
- [2086] A required model asset is missing.
- [2087] A required model asset is damaged.
- [2088] Microphone permission is denied.
- [2089] Audio-output permission is denied.
- [2090] The audio device is unavailable.
- [2091] Provider authentication fails.
- [2092] The provider network request fails.
- [2093] The selected model is unsupported.
- [2094] The selected device is unsupported.

### Store durable facts with phrases such as “remember that,” “note that,” or “keep in mind.”

- [2095] Memory is disabled.
- [2096] The relevant data store is locked.
- [2097] The relevant data store is corrupt.
- [x] [2098] The memory fact is rejected. [T045]
- [2099] The memory fact duplicates an existing fact.
- [2100] Project scope is wrong.
- [2101] Retrieval is empty.
- [2102] The memory model route fails.

### Forget/remove remembered facts through supported memory commands.

- [2103] Memory is disabled.
- [2104] The relevant data store is locked.
- [2105] The relevant data store is corrupt.
- [2106] The memory fact is rejected.
- [2107] The memory fact duplicates an existing fact.
- [2108] Project scope is wrong.
- [2109] Retrieval is empty.
- [2110] The memory model route fails.

### Let the model search memory when a prompt needs it.

- [2111] Memory is disabled.
- [2112] The relevant data store is locked.
- [2113] The relevant data store is corrupt.
- [2114] The memory fact is rejected.
- [2115] The memory fact duplicates an existing fact.
- [2116] Project scope is wrong.
- [x] [2117] Retrieval is empty. [T046]
- [2118] The memory model route fails.

### Let the model save a durable memory when permitted.

- [2119] Memory is disabled.
- [2120] The relevant data store is locked.
- [2121] The relevant data store is corrupt.
- [x] [2122] The memory fact is rejected. [T045]
- [2123] The memory fact duplicates an existing fact.
- [2124] Project scope is wrong.
- [2125] Retrieval is empty.
- [2126] The memory model route fails.

### Automatically extract long-term facts from conversations.

- [2127] Memory is disabled.
- [2128] The relevant data store is locked.
- [2129] The relevant data store is corrupt.
- [x] [2130] The memory fact is rejected. [T045]
- [2131] The memory fact duplicates an existing fact.
- [2132] Project scope is wrong.
- [2133] Retrieval is empty.
- [2134] The memory model route fails.

### Automatically consolidate memory on a configurable interval.

- [2135] Memory is disabled.
- [2136] The relevant data store is locked.
- [2137] The relevant data store is corrupt.
- [2138] The memory fact is rejected.
- [2139] The memory fact duplicates an existing fact.
- [2140] Project scope is wrong.
- [2141] Retrieval is empty.
- [2142] The memory model route fails.

### Set how many facts are retrieved for a query.

- [2143] Memory is disabled.
- [2144] The relevant data store is locked.
- [2145] The relevant data store is corrupt.
- [2146] The memory fact is rejected.
- [2147] The memory fact duplicates an existing fact.
- [2148] Project scope is wrong.
- [2149] Retrieval is empty.
- [2150] The memory model route fails.

### Set the short-term-memory token budget before compression.

- [2151] Memory is disabled.
- [2152] The relevant data store is locked.
- [2153] The relevant data store is corrupt.
- [2154] The memory fact is rejected.
- [2155] The memory fact duplicates an existing fact.
- [2156] Project scope is wrong.
- [2157] Retrieval is empty.
- [2158] The memory model route fails.

### Keep General memory separate from project memory.

- [2159] Memory is disabled.
- [2160] The relevant data store is locked.
- [2161] The relevant data store is corrupt.
- [2162] The memory fact is rejected.
- [2163] The memory fact duplicates an existing fact.
- [2164] Project scope is wrong.
- [2165] Retrieval is empty.
- [2166] The memory model route fails.

### Open the Long-term Memory viewer.

- [2167] Memory is disabled.
- [2168] The relevant data store is locked.
- [2169] The relevant data store is corrupt.
- [2170] The memory fact is rejected.
- [2171] The memory fact duplicates an existing fact.
- [2172] Project scope is wrong.
- [2173] Retrieval is empty.
- [2174] The memory model route fails.

### View facts grouped by project.

- [2175] Memory is disabled.
- [2176] The relevant data store is locked.
- [2177] The relevant data store is corrupt.
- [2178] The memory fact is rejected.
- [2179] The memory fact duplicates an existing fact.
- [2180] Project scope is wrong.
- [2181] Retrieval is empty.
- [2182] The memory model route fails.

### Add a fact manually and choose its project.

- [x] [2183] The new value is empty. [T047]
- [2184] The new value is invalid.
- [2185] The new value duplicates an existing value.
- [2186] The backing store is read-only.
- [2187] The backing store is locked.
- [2188] The backing store is corrupt.
- [2189] The write is interrupted.

### Click and edit a fact.

- [2190] Memory is disabled.
- [2191] The relevant data store is locked.
- [2192] The relevant data store is corrupt.
- [x] [2193] The memory fact is rejected. [T045][T048]
- [2194] The memory fact duplicates an existing fact.
- [2195] Project scope is wrong.
- [2196] Retrieval is empty.
- [2197] The memory model route fails.

### Move/change a fact's project.

- [2198] The new value is empty.
- [2199] The new value is invalid.
- [2200] The new value duplicates an existing value.
- [2201] The backing store is read-only.
- [2202] The backing store is locked.
- [2203] The backing store is corrupt.
- [2204] The write is interrupted.

### Delete a fact.

- [x] [2205] A target required by this function is missing. [T049]
- [2206] A target required by this function is locked.
- [2207] Confirmation is cancelled.
- [2208] Required elevation is denied.
- [2209] Storage access is denied.
- [2210] Another process is using the files.
- [2211] Cleanup only partly completes.

### Refresh the memory list.

- [2212] Memory is disabled.
- [2213] The relevant data store is locked.
- [2214] The relevant data store is corrupt.
- [2215] The memory fact is rejected.
- [2216] The memory fact duplicates an existing fact.
- [2217] Project scope is wrong.
- [2218] Retrieval is empty.
- [2219] The memory model route fails.

### Send messages with privacy filtering off.

- [2220] The selected privacy filter is unavailable.
- [2221] The selected privacy runtime is unavailable.
- [2222] Model assets are missing.
- [2223] Private-information detection times out.
- [2224] Private-information detection misclassifies content.
- [2225] The privacy review is cancelled.
- [2226] Redaction configuration is invalid.

### Redact private information with the built-in local filter.

- [2227] The selected privacy filter is unavailable.
- [2228] The selected privacy runtime is unavailable.
- [2229] Model assets are missing.
- [2230] Private-information detection times out.
- [x] [2231] Private-information detection misclassifies content. [T052]
- [2232] The privacy review is cancelled.
- [2233] Redaction configuration is invalid.

### Install, repair, and use the advanced local privacy model.

- [2234] Network access is unavailable.
- [2235] The package source is unavailable.
- [2236] Available disk space is insufficient.
- [2237] Filesystem permission is insufficient.
- [2238] Verification fails.
- [2239] Dependency versions conflict.
- [2240] The operation is cancelled.

### Remove the advanced privacy model.

- [2241] A target required by this function is missing.
- [2242] A target required by this function is locked.
- [2243] Confirmation is cancelled.
- [2244] Required elevation is denied.
- [2245] Storage access is denied.
- [2246] Another process is using the files.
- [2247] Cleanup only partly completes.

### Keep the built-in filter active alongside Advanced mode.

- [2248] The selected privacy filter is unavailable.
- [2249] The selected privacy runtime is unavailable.
- [2250] Model assets are missing.
- [2251] Private-information detection times out.
- [2252] Private-information detection misclassifies content.
- [2253] The privacy review is cancelled.
- [2254] Redaction configuration is invalid.

### Review detected private information and the redacted request before sending.

- [2255] The selected privacy filter is unavailable.
- [2256] The selected privacy runtime is unavailable.
- [2257] Model assets are missing.
- [2258] Private-information detection times out.
- [2259] Private-information detection misclassifies content.
- [x] [2260] The privacy review is cancelled. [T050]
- [2261] Redaction configuration is invalid.

### Cancel a request from the privacy review.

- [2262] The selected privacy filter is unavailable.
- [2263] The selected privacy runtime is unavailable.
- [2264] Model assets are missing.
- [2265] Private-information detection times out.
- [2266] Private-information detection misclassifies content.
- [x] [2267] The privacy review is cancelled. [T050]
- [2268] Redaction configuration is invalid.

### Apply privacy handling to normal requests, tool results, and local-model requests.

- [2269] The selected privacy filter is unavailable.
- [x] [2270] The selected privacy runtime is unavailable. [T051]
- [2271] Model assets are missing.
- [2272] Private-information detection times out.
- [2273] Private-information detection misclassifies content.
- [x] [2274] The privacy review is cancelled. [T050]
- [2275] Redaction configuration is invalid.

### Store provider credentials outside the plain settings file in the OS keychain.

- [2276] A value required by this function is invalid.
- [2277] The OS keychain is unavailable.
- [2278] The endpoint URL is malformed.
- [2279] The endpoint is offline.
- [2280] The account lacks permission.
- [2281] The setting cannot be saved.

### Mask stored secrets in Settings.

- [2282] A value required by this function is invalid.
- [2283] The OS keychain is unavailable.
- [2284] The endpoint URL is malformed.
- [2285] The endpoint is offline.
- [2286] The account lacks permission.
- [2287] The setting cannot be saved.

### Reset all settings and remove saved keychain secrets after confirmation.

- [2288] A target required by this function is missing.
- [2289] A target required by this function is locked.
- [2290] Confirmation is cancelled.
- [2291] Required elevation is denied.
- [2292] Storage access is denied.
- [2293] Another process is using the files.
- [2294] Cleanup only partly completes.

### Search the web with `web_search`.

- [2295] Network access is disabled.
- [2296] The required tool is disabled.
- [2297] The page blocks retrieval.
- [2298] No browser source is detected.
- [2299] The response exceeds context limits.
- [2300] The remote format changes.

### Read open documents/current pages with `get_context`.

- [2301] The source application is unsupported.
- [2302] The source application is closed.
- [2303] Accessibility permission is missing.
- [2304] Automation permission is missing.
- [2305] Extraction returns no text.
- [2306] Content is truncated by its budget.

### Retrieve a specific website with `retrieve_website`.

- [2307] Network access is disabled.
- [2308] The required tool is disabled.
- [2309] The page blocks retrieval.
- [2310] No browser source is detected.
- [2311] The response exceeds context limits.
- [2312] The remote format changes.

### Capture the screen with `capture_screen`.

- [2313] The tool is disabled.
- [2314] The tool scope is denied.
- [2315] The tool permission is denied.
- [2316] The tool inputs are invalid.
- [2317] The tool-call budget is exhausted.
- [2318] The tool-output budget is exhausted.

### Search long-term memory with `memory_search`.

- [2319] Memory is disabled.
- [2320] The relevant data store is locked.
- [2321] The relevant data store is corrupt.
- [2322] The memory fact is rejected.
- [2323] The memory fact duplicates an existing fact.
- [2324] Project scope is wrong.
- [2325] Retrieval is empty.
- [2326] The memory model route fails.

### Save durable memory with `memory_save`.

- [2327] The tool is disabled.
- [2328] The tool scope is denied.
- [2329] The tool permission is denied.
- [2330] The tool inputs are invalid.
- [2331] The tool-call budget is exhausted.
- [2332] The tool-output budget is exhausted.

### Read local Git status with `git_status`.

- [2333] Git is unavailable.
- [2334] The selected folder is not a repository.
- [2335] The working tree cannot be read.
- [2336] The scope is wrong.
- [2337] The output budget is exceeded.

### Read local Git diff with `git_diff`.

- [2338] Git is unavailable.
- [2339] The selected folder is not a repository.
- [2340] The working tree cannot be read.
- [2341] The scope is wrong.
- [2342] The output budget is exceeded.

### List allowed folders with `list_files`.

- [x] [2343] The path is outside configured allowed roots. [T053][T054]
- [2344] The path matches a blocked glob.
- [2345] A target required by this function is missing.
- [2346] A target required by this function is locked.
- [2347] OS access is denied.
- [2348] Approval is declined.
- [2349] A concurrent change invalidates the operation.

### Read allowed files with `read_file`.

- [x] [2350] The path is outside configured allowed roots. [T053][T054]
- [x] [2351] The path matches a blocked glob. [T055]
- [2352] A target required by this function is missing.
- [2353] A target required by this function is locked.
- [2354] OS access is denied.
- [2355] Approval is declined.
- [2356] A concurrent change invalidates the operation.

### Create allowed files with `create_file`.

- [x] [2357] The path is outside configured allowed roots. [T053][T054]
- [2358] The path matches a blocked glob.
- [2359] A target required by this function is missing.
- [2360] A target required by this function is locked.
- [2361] OS access is denied.
- [2362] Approval is declined.
- [2363] A concurrent change invalidates the operation.

### Patch allowed files with `edit_file`.

- [x] [2364] The path is outside configured allowed roots. [T053][T054]
- [2365] The path matches a blocked glob.
- [2366] A target required by this function is missing.
- [2367] A target required by this function is locked.
- [2368] OS access is denied.
- [2369] Approval is declined.
- [x] [2370] A concurrent change invalidates the operation. [T056]

### Create or overwrite allowed files with `write_file`.

- [x] [2371] The path is outside configured allowed roots. [T053][T054]
- [2372] The path matches a blocked glob.
- [2373] A target required by this function is missing.
- [2374] A target required by this function is locked.
- [2375] OS access is denied.
- [2376] Approval is declined.
- [2377] A concurrent change invalidates the operation.

### Fetch GitHub repository metadata with `github_repo`.

- [2378] GitHub authentication is missing.
- [2379] A required GitHub OAuth scope is missing.
- [2380] The requested GitHub resource is private and inaccessible.
- [2381] The requested GitHub resource does not exist.
- [2382] Network access is unavailable.
- [2383] The remote API is unavailable.
- [2384] Its identifier is invalid.

### Fetch a GitHub issue or pull request with `github_issue`.

- [2385] GitHub authentication is missing.
- [2386] A required GitHub OAuth scope is missing.
- [2387] The requested GitHub resource is private and inaccessible.
- [2388] The requested GitHub resource does not exist.
- [2389] Network access is unavailable.
- [2390] The remote API is unavailable.
- [2391] Its identifier is invalid.

### Set each local-file tool to Off or Auto for an individual shortcut.

- [2392] The key binding is invalid.
- [2393] The key binding conflicts with another binding.
- [2394] The OS rejects the global hook.
- [2395] Input-monitoring permission is missing.
- [2396] Accessibility permission is missing.
- [2397] Another application consumes the event.

### Set installed/add-on tools to Off or Auto for an individual shortcut.

- [2398] The key binding is invalid.
- [2399] The key binding conflicts with another binding.
- [2400] The OS rejects the global hook.
- [2401] Input-monitoring permission is missing.
- [2402] Accessibility permission is missing.
- [2403] Another application consumes the event.

### Group MCP tools by server and enable/disable a complete server.

- [2404] The required MCP component is disabled.
- [2405] The required MCP component is incompatible.
- [2406] The component manifest is invalid.
- [2407] The component permission configuration is invalid.
- [2408] Dependencies are missing.
- [2409] The isolated host crashes.
- [2410] The MCP server is unavailable.
- [2411] The MCP protocol is unavailable.

### Override an individual MCP tool or let it follow its server setting.

- [2412] The required MCP component is disabled.
- [2413] The required MCP component is incompatible.
- [2414] The component manifest is invalid.
- [2415] The component permission configuration is invalid.
- [2416] Dependencies are missing.
- [2417] The isolated host crashes.
- [2418] The MCP server is unavailable.
- [2419] The MCP protocol is unavailable.

### Ask for approval and show a diff before file writes when configured.

- [2420] The path is outside configured allowed roots.
- [2421] The path matches a blocked glob.
- [2422] A target required by this function is missing.
- [2423] A target required by this function is locked.
- [2424] OS access is denied.
- [2425] Approval is declined.
- [2426] A concurrent change invalidates the operation.

### Approve a proposed live file operation.

- [2427] The path is outside configured allowed roots.
- [2428] The path matches a blocked glob.
- [2429] A target required by this function is missing.
- [2430] A target required by this function is locked.
- [2431] OS access is denied.
- [2432] Approval is declined.
- [2433] A concurrent change invalidates the operation.

### Request an alternate operation and provide feedback.

- [2434] The tool is disabled.
- [2435] The tool scope is denied.
- [2436] The tool permission is denied.
- [2437] The tool inputs are invalid.
- [2438] The tool-call budget is exhausted.
- [2439] The tool-output budget is exhausted.

### Decline a proposed live file operation and provide feedback.

- [2440] The path is outside configured allowed roots.
- [2441] The path matches a blocked glob.
- [2442] A target required by this function is missing.
- [2443] A target required by this function is locked.
- [2444] OS access is denied.
- [2445] Approval is declined.
- [2446] A concurrent change invalidates the operation.

### Enforce per-request tool-call and tool-output budgets.

- [2447] The tool is disabled.
- [2448] The tool scope is denied.
- [2449] The tool permission is denied.
- [2450] The tool inputs are invalid.
- [2451] The tool-call budget is exhausted.
- [x] [2452] The tool-output budget is exhausted. [T057]

### Show tool activity/trace in chat when enabled.

- [2453] The conversation store is locked.
- [2454] The conversation store is corrupt.
- [2455] The selected conversation record is stale.
- [2456] A stream is still active.
- [2457] The configured model fails.
- [2458] The configured tool fails.
- [2459] Persistence is interrupted.

### Open the Addon Manager.

- [2460] The required component is disabled.
- [2461] The required component is incompatible.
- [2462] The component manifest is invalid.
- [2463] The component permission configuration is invalid.
- [2464] Dependencies are missing.
- [2465] The isolated host crashes.
- [2466] The MCP server is unavailable.
- [2467] The MCP protocol is unavailable.

### Open the add-ons folder.

- [2468] The path no longer exists.
- [2469] Access is denied.
- [2470] The OS file-manager command is unavailable.
- [2471] The platform cannot reveal that item.

### Install an add-on archive.

- [2472] Network access is unavailable.
- [2473] The package source is unavailable.
- [2474] Available disk space is insufficient.
- [2475] Filesystem permission is insufficient.
- [x] [2476] Verification fails. [T058]
- [2477] Dependency versions conflict.
- [x] [2478] The operation is cancelled. [T059]

### Install an add-on from a folder.

- [2479] Network access is unavailable.
- [2480] The package source is unavailable.
- [2481] Available disk space is insufficient.
- [2482] Filesystem permission is insufficient.
- [2483] Verification fails.
- [2484] Dependency versions conflict.
- [2485] The operation is cancelled.

### Enable or disable an installed add-on.

- [2486] The required component is disabled.
- [2487] The required component is incompatible.
- [2488] The component manifest is invalid.
- [2489] The component permission configuration is invalid.
- [x] [2490] Dependencies are missing. [T060]
- [2491] The isolated host crashes.
- [2492] The MCP server is unavailable.
- [2493] The MCP protocol is unavailable.

### Open an add-on's settings.

- [2494] The required component is disabled.
- [2495] The required component is incompatible.
- [2496] The component manifest is invalid.
- [2497] The component permission configuration is invalid.
- [2498] Dependencies are missing.
- [2499] The isolated host crashes.
- [2500] The MCP server is unavailable.
- [2501] The MCP protocol is unavailable.

### Edit checkbox, number, text, and choice settings supplied by an add-on.

- [2502] The required component is disabled.
- [2503] The required component is incompatible.
- [2504] The component manifest is invalid.
- [2505] The component permission configuration is invalid.
- [2506] Dependencies are missing.
- [2507] The isolated host crashes.
- [2508] The MCP server is unavailable.
- [2509] The MCP protocol is unavailable.

### Open and review an add-on's logs.

- [2510] The path no longer exists.
- [2511] Access is denied.
- [2512] The OS file-manager command is unavailable.
- [2513] The platform cannot reveal that item.

### Install, rebuild, or repair an add-on's isolated dependencies after approval.

- [2514] Network access is unavailable.
- [2515] The package source is unavailable.
- [2516] Available disk space is insufficient.
- [2517] Filesystem permission is insufficient.
- [2518] Verification fails.
- [2519] Dependency versions conflict.
- [2520] The operation is cancelled.

### Run add-ons in isolated host processes.

- [2521] The required component is disabled.
- [2522] The required component is incompatible.
- [2523] The component manifest is invalid.
- [2524] The component permission configuration is invalid.
- [x] [2525] Dependencies are missing. [T060]
- [2526] The isolated host crashes.
- [2527] The MCP server is unavailable.
- [2528] The MCP protocol is unavailable.

### Enforce permissions declared by an add-on manifest.

- [2529] The required component is disabled.
- [2530] The required component is incompatible.
- [2531] The component manifest is invalid.
- [x] [2532] The component permission configuration is invalid. [T061]
- [2533] Dependencies are missing.
- [2534] The isolated host crashes.
- [2535] The MCP server is unavailable.
- [2536] The MCP protocol is unavailable.

### Let add-ons contribute context before a query.

- [2537] The required component is disabled.
- [2538] The required component is incompatible.
- [2539] The component manifest is invalid.
- [x] [2540] The component permission configuration is invalid. [T061]
- [2541] Dependencies are missing.
- [2542] The isolated host crashes.
- [2543] The MCP server is unavailable.
- [2544] The MCP protocol is unavailable.

### Let add-ons contribute model-callable tools.

- [2545] The required component is disabled.
- [2546] The required component is incompatible.
- [2547] The component manifest is invalid.
- [x] [2548] The component permission configuration is invalid. [T061]
- [2549] Dependencies are missing.
- [2550] The isolated host crashes.
- [2551] The MCP server is unavailable.
- [2552] The MCP protocol is unavailable.

### Let add-ons process a response after completion.

- [2553] The required component is disabled.
- [2554] The required component is incompatible.
- [2555] The component manifest is invalid.
- [2556] The component permission configuration is invalid.
- [2557] Dependencies are missing.
- [2558] The isolated host crashes.
- [2559] The MCP server is unavailable.
- [2560] The MCP protocol is unavailable.

### Let add-ons add intent actions and action rows.

- [2561] The required component is disabled.
- [2562] The required component is incompatible.
- [2563] The component manifest is invalid.
- [2564] The component permission configuration is invalid.
- [2565] Dependencies are missing.
- [2566] The isolated host crashes.
- [2567] The MCP server is unavailable.
- [2568] The MCP protocol is unavailable.

### Let add-ons add global shortcuts.

- [2569] The key binding is invalid.
- [2570] The key binding conflicts with another binding.
- [2571] The OS rejects the global hook.
- [2572] Input-monitoring permission is missing.
- [2573] Accessibility permission is missing.
- [2574] Another application consumes the event.

### Let add-ons add tray actions and notifications.

- [2575] The required component is disabled.
- [2576] The required component is incompatible.
- [2577] The component manifest is invalid.
- [x] [2578] The component permission configuration is invalid. [T061]
- [2579] Dependencies are missing.
- [2580] The isolated host crashes.
- [2581] The MCP server is unavailable.
- [2582] The MCP protocol is unavailable.

### Let add-ons add Settings fields.

- [2583] The required component is disabled.
- [2584] The required component is incompatible.
- [2585] The component manifest is invalid.
- [x] [2586] The component permission configuration is invalid. [T061]
- [2587] Dependencies are missing.
- [2588] The isolated host crashes.
- [2589] The MCP server is unavailable.
- [2590] The MCP protocol is unavailable.

### Let add-ons perform capped auxiliary LLM actions.

- [2591] The required component is disabled.
- [2592] The required component is incompatible.
- [2593] The component manifest is invalid.
- [2594] The component permission configuration is invalid.
- [2595] Dependencies are missing.
- [2596] The isolated host crashes.
- [2597] The MCP server is unavailable.
- [2598] The MCP protocol is unavailable.

### Discover configured MCP servers and expose their tools through the bridge.

- [2599] The required MCP component is disabled.
- [2600] The required MCP component is incompatible.
- [2601] The component manifest is invalid.
- [2602] The component permission configuration is invalid.
- [x] [2603] Dependencies are missing. [T067]
- [2604] The isolated host crashes.
- [2605] The MCP server is unavailable.
- [2606] The MCP protocol is unavailable.

### Expose Wisp context MCP operations for selected text, clipboard, active window, browser page, and screen snip.

- [x] [2607] Nothing is selected. [T066]
- [2608] Focus moved.
- [2609] The target control does not expose accessible text.
- [2610] The OS permission is missing.
- [2611] The target application is unsupported.
- [2612] The platform backend is unsupported.

### Start an agent task from the tray menu.

- [2613] The task specification is invalid.
- [2614] The selected agent model is invalid.
- [2615] The task scope is invalid.
- [x] [2616] A required credential is missing. [T063]
- [2617] A required permission is missing.
- [2618] The agent runtime exits unexpectedly.
- [2619] Concurrent agent execution conflicts.
- [2620] A file lease conflicts with another agent.
- [2621] Saved task artifacts are unavailable.

### Copy task settings from the last agent task.

- [2622] The task specification is invalid.
- [2623] The selected agent model is invalid.
- [2624] The task scope is invalid.
- [2625] A required credential is missing.
- [2626] A required permission is missing.
- [2627] The agent runtime exits unexpectedly.
- [2628] Concurrent agent execution conflicts.
- [2629] A file lease conflicts with another agent.
- [2630] Saved task artifacts are unavailable.

### Set the task title and objective.

- [2631] The task specification is invalid.
- [2632] The selected agent model is invalid.
- [2633] The task scope is invalid.
- [2634] A required credential is missing.
- [2635] A required permission is missing.
- [2636] The agent runtime exits unexpectedly.
- [2637] Concurrent agent execution conflicts.
- [2638] A file lease conflicts with another agent.
- [2639] Saved task artifacts are unavailable.

### Copy relevant context from the current application.

- [2640] The source application is unsupported.
- [2641] The source application is closed.
- [2642] Accessibility permission is missing.
- [2643] Automation permission is missing.
- [2644] Extraction returns no text.
- [2645] Content is truncated by its budget.

### Add required task context manually.

- [2646] The task specification is invalid.
- [2647] The selected agent model is invalid.
- [2648] The task scope is invalid.
- [2649] A required credential is missing.
- [2650] A required permission is missing.
- [2651] The agent runtime exits unexpectedly.
- [2652] Concurrent agent execution conflicts.
- [2653] A file lease conflicts with another agent.
- [2654] Saved task artifacts are unavailable.

### Choose an agent provider/model and fallback models.

- [2655] The task specification is invalid.
- [2656] The selected agent model is invalid.
- [2657] The task scope is invalid.
- [2658] A required credential is missing.
- [2659] A required permission is missing.
- [2660] The agent runtime exits unexpectedly.
- [2661] Concurrent agent execution conflicts.
- [2662] A file lease conflicts with another agent.
- [2663] Saved task artifacts are unavailable.

### Choose the task scope folder.

- [2664] The task specification is invalid.
- [2665] The selected agent model is invalid.
- [x] [2666] The task scope is invalid. [T062]
- [2667] A required credential is missing.
- [2668] A required permission is missing.
- [2669] The agent runtime exits unexpectedly.
- [2670] Concurrent agent execution conflicts.
- [2671] A file lease conflicts with another agent.
- [2672] Saved task artifacts are unavailable.

### Configure allowed and blocked file globs.

- [2673] The task specification is invalid.
- [2674] The selected agent model is invalid.
- [2675] The task scope is invalid.
- [2676] A required credential is missing.
- [2677] A required permission is missing.
- [2678] The agent runtime exits unexpectedly.
- [2679] Concurrent agent execution conflicts.
- [2680] A file lease conflicts with another agent.
- [2681] Saved task artifacts are unavailable.

### Use the coordinator, builder, and reviewer defaults.

- [2682] The task specification is invalid.
- [2683] The selected agent model is invalid.
- [2684] The task scope is invalid.
- [2685] A required credential is missing.
- [2686] A required permission is missing.
- [2687] The agent runtime exits unexpectedly.
- [2688] Concurrent agent execution conflicts.
- [2689] A file lease conflicts with another agent.
- [2690] Saved task artifacts are unavailable.

### Add or remove agents.

- [2691] The task specification is invalid.
- [2692] The selected agent model is invalid.
- [2693] The task scope is invalid.
- [2694] A required credential is missing.
- [2695] A required permission is missing.
- [2696] The agent runtime exits unexpectedly.
- [2697] Concurrent agent execution conflicts.
- [2698] A file lease conflicts with another agent.
- [2699] Saved task artifacts are unavailable.

### Customize each agent's name, role, model, and instructions.

- [2700] The task specification is invalid.
- [2701] The selected agent model is invalid.
- [2702] The task scope is invalid.
- [2703] A required credential is missing.
- [2704] A required permission is missing.
- [2705] The agent runtime exits unexpectedly.
- [2706] Concurrent agent execution conflicts.
- [2707] A file lease conflicts with another agent.
- [2708] Saved task artifacts are unavailable.

### Start with a parallel read-only briefing.

- [2709] The task specification is invalid.
- [2710] The selected agent model is invalid.
- [2711] The task scope is invalid.
- [2712] A required credential is missing.
- [2713] A required permission is missing.
- [2714] The agent runtime exits unexpectedly.
- [2715] Concurrent agent execution conflicts.
- [2716] A file lease conflicts with another agent.
- [2717] Saved task artifacts are unavailable.

### Run implementer agents in parallel with file leases.

- [2718] The task specification is invalid.
- [2719] The selected agent model is invalid.
- [2720] The task scope is invalid.
- [2721] A required credential is missing.
- [2722] A required permission is missing.
- [2723] The agent runtime exits unexpectedly.
- [2724] Concurrent agent execution conflicts.
- [x] [2725] A file lease conflicts with another agent. [T064]
- [2726] Saved task artifacts are unavailable.

### Add or remove agent-to-agent communications.

- [2727] The task specification is invalid.
- [2728] The selected agent model is invalid.
- [2729] The task scope is invalid.
- [2730] A required credential is missing.
- [2731] A required permission is missing.
- [2732] The agent runtime exits unexpectedly.
- [2733] Concurrent agent execution conflicts.
- [2734] A file lease conflicts with another agent.
- [2735] Saved task artifacts are unavailable.

### Configure sender, recipient, trigger, and message for a communication.

- [2736] The task specification is invalid.
- [2737] The selected agent model is invalid.
- [2738] The task scope is invalid.
- [2739] A required credential is missing.
- [2740] A required permission is missing.
- [2741] The agent runtime exits unexpectedly.
- [2742] Concurrent agent execution conflicts.
- [2743] A file lease conflicts with another agent.
- [2744] Saved task artifacts are unavailable.

### Create paired two-way exchanges.

- [2745] The task specification is invalid.
- [2746] The selected agent model is invalid.
- [2747] The task scope is invalid.
- [2748] A required credential is missing.
- [2749] A required permission is missing.
- [2750] The agent runtime exits unexpectedly.
- [2751] Concurrent agent execution conflicts.
- [2752] A file lease conflicts with another agent.
- [2753] Saved task artifacts are unavailable.

### Reset the communication map to defaults.

- [2754] The task specification is invalid.
- [2755] The selected agent model is invalid.
- [2756] The task scope is invalid.
- [2757] A required credential is missing.
- [2758] A required permission is missing.
- [2759] The agent runtime exits unexpectedly.
- [2760] Concurrent agent execution conflicts.
- [2761] A file lease conflicts with another agent.
- [2762] Saved task artifacts are unavailable.

### Open and refresh the Agent Communication Map.

- [2763] The task specification is invalid.
- [2764] The selected agent model is invalid.
- [2765] The task scope is invalid.
- [2766] A required credential is missing.
- [2767] A required permission is missing.
- [2768] The agent runtime exits unexpectedly.
- [2769] Concurrent agent execution conflicts.
- [2770] A file lease conflicts with another agent.
- [2771] Saved task artifacts are unavailable.

### Set explicit completion criteria.

- [2772] The task specification is invalid.
- [2773] The selected agent model is invalid.
- [2774] The task scope is invalid.
- [2775] A required credential is missing.
- [2776] A required permission is missing.
- [2777] The agent runtime exits unexpectedly.
- [2778] Concurrent agent execution conflicts.
- [2779] A file lease conflicts with another agent.
- [2780] Saved task artifacts are unavailable.

### Preview the generated task specification.

- [2781] The task specification is invalid.
- [2782] The selected agent model is invalid.
- [2783] The task scope is invalid.
- [2784] A required credential is missing.
- [2785] A required permission is missing.
- [2786] The agent runtime exits unexpectedly.
- [2787] Concurrent agent execution conflicts.
- [2788] A file lease conflicts with another agent.
- [2789] Saved task artifacts are unavailable.

### Start or cancel the configured task.

- [2790] The task specification is invalid.
- [2791] The selected agent model is invalid.
- [2792] The task scope is invalid.
- [2793] A required credential is missing.
- [2794] A required permission is missing.
- [2795] The agent runtime exits unexpectedly.
- [2796] Concurrent agent execution conflicts.
- [2797] A file lease conflicts with another agent.
- [2798] Saved task artifacts are unavailable.

### Review and approve an agent permission request.

- [2799] The task specification is invalid.
- [2800] The selected agent model is invalid.
- [2801] The task scope is invalid.
- [2802] A required credential is missing.
- [2803] A required permission is missing.
- [2804] The agent runtime exits unexpectedly.
- [2805] Concurrent agent execution conflicts.
- [2806] A file lease conflicts with another agent.
- [2807] Saved task artifacts are unavailable.

### Decline an agent permission request.

- [2808] The task specification is invalid.
- [2809] The selected agent model is invalid.
- [2810] The task scope is invalid.
- [2811] A required credential is missing.
- [2812] A required permission is missing.
- [2813] The agent runtime exits unexpectedly.
- [2814] Concurrent agent execution conflicts.
- [2815] A file lease conflicts with another agent.
- [2816] Saved task artifacts are unavailable.

### Watch the live Meeting view and agent cards.

- [2817] The task specification is invalid.
- [2818] The selected agent model is invalid.
- [2819] The task scope is invalid.
- [2820] A required credential is missing.
- [2821] A required permission is missing.
- [2822] The agent runtime exits unexpectedly.
- [2823] Concurrent agent execution conflicts.
- [2824] A file lease conflicts with another agent.
- [2825] Saved task artifacts are unavailable.

### View Live Log, Model Trace, and Final Report tabs.

- [2826] The task specification is invalid.
- [2827] The selected agent model is invalid.
- [2828] The task scope is invalid.
- [2829] A required credential is missing.
- [2830] A required permission is missing.
- [2831] The agent runtime exits unexpectedly.
- [2832] Concurrent agent execution conflicts.
- [2833] A file lease conflicts with another agent.
- [2834] Saved task artifacts are unavailable.

### Drag/resize agent cards and reset their layout.

- [2835] The task specification is invalid.
- [2836] The selected agent model is invalid.
- [2837] The task scope is invalid.
- [2838] A required credential is missing.
- [2839] A required permission is missing.
- [2840] The agent runtime exits unexpectedly.
- [2841] Concurrent agent execution conflicts.
- [2842] A file lease conflicts with another agent.
- [2843] Saved task artifacts are unavailable.

### Inspect agent status, health, messages, and shared-board activity.

- [2844] The task specification is invalid.
- [2845] The selected agent model is invalid.
- [2846] The task scope is invalid.
- [2847] A required credential is missing.
- [2848] A required permission is missing.
- [2849] The agent runtime exits unexpectedly.
- [2850] Concurrent agent execution conflicts.
- [2851] A file lease conflicts with another agent.
- [2852] Saved task artifacts are unavailable.

### View the task diff.

- [2853] The task specification is invalid.
- [2854] The selected agent model is invalid.
- [2855] The task scope is invalid.
- [2856] A required credential is missing.
- [2857] A required permission is missing.
- [2858] The agent runtime exits unexpectedly.
- [2859] Concurrent agent execution conflicts.
- [2860] A file lease conflicts with another agent.
- [2861] Saved task artifacts are unavailable.

### Open the task memory/result folder.

- [2862] The path no longer exists.
- [2863] Access is denied.
- [2864] The OS file-manager command is unavailable.
- [2865] The platform cannot reveal that item.

### Open the task scope folder.

- [2866] The path no longer exists.
- [2867] Access is denied.
- [2868] The OS file-manager command is unavailable.
- [2869] The platform cannot reveal that item.

### Retry a task.

- [2870] The task specification is invalid.
- [2871] The selected agent model is invalid.
- [2872] The task scope is invalid.
- [2873] A required credential is missing.
- [2874] A required permission is missing.
- [2875] The agent runtime exits unexpectedly.
- [2876] Concurrent agent execution conflicts.
- [2877] A file lease conflicts with another agent.
- [2878] Saved task artifacts are unavailable.

### Continue a completed or interrupted task.

- [2879] The task specification is invalid.
- [2880] The selected agent model is invalid.
- [2881] The task scope is invalid.
- [2882] A required credential is missing.
- [2883] A required permission is missing.
- [2884] The agent runtime exits unexpectedly.
- [2885] Concurrent agent execution conflicts.
- [2886] A file lease conflicts with another agent.
- [2887] Saved task artifacts are unavailable.

### Nudge a selected agent with a message.

- [2888] The task specification is invalid.
- [2889] The selected agent model is invalid.
- [2890] The task scope is invalid.
- [2891] A required credential is missing.
- [2892] A required permission is missing.
- [2893] The agent runtime exits unexpectedly.
- [2894] Concurrent agent execution conflicts.
- [2895] A file lease conflicts with another agent.
- [2896] Saved task artifacts are unavailable.

### Pause after the current turn and resume later.

- [2897] The task specification is invalid.
- [2898] The selected agent model is invalid.
- [2899] The task scope is invalid.
- [2900] A required credential is missing.
- [2901] A required permission is missing.
- [2902] The agent runtime exits unexpectedly.
- [2903] Concurrent agent execution conflicts.
- [2904] A file lease conflicts with another agent.
- [2905] Saved task artifacts are unavailable.

### Cancel a running task.

- [2906] The task specification is invalid.
- [2907] The selected agent model is invalid.
- [2908] The task scope is invalid.
- [2909] A required credential is missing.
- [2910] A required permission is missing.
- [2911] The agent runtime exits unexpectedly.
- [2912] Concurrent agent execution conflicts.
- [2913] A file lease conflicts with another agent.
- [2914] Saved task artifacts are unavailable.

### Open Agent Task History.

- [2915] The task specification is invalid.
- [2916] The selected agent model is invalid.
- [2917] The task scope is invalid.
- [2918] A required credential is missing.
- [2919] A required permission is missing.
- [2920] The agent runtime exits unexpectedly.
- [2921] Concurrent agent execution conflicts.
- [2922] A file lease conflicts with another agent.
- [2923] Saved task artifacts are unavailable.

### Review a historical task's Summary, Run Log, Model Trace, and Diff.

- [2924] The task specification is invalid.
- [2925] The selected agent model is invalid.
- [2926] The task scope is invalid.
- [2927] A required credential is missing.
- [2928] A required permission is missing.
- [2929] The agent runtime exits unexpectedly.
- [2930] Concurrent agent execution conflicts.
- [2931] A file lease conflicts with another agent.
- [2932] Saved task artifacts are unavailable.

### Refresh history and reopen result folders.

- [2933] The task specification is invalid.
- [2934] The selected agent model is invalid.
- [2935] The task scope is invalid.
- [2936] A required credential is missing.
- [2937] A required permission is missing.
- [2938] The agent runtime exits unexpectedly.
- [2939] Concurrent agent execution conflicts.
- [2940] A file lease conflicts with another agent.
- [2941] Saved task artifacts are unavailable.

### Navigate General, Connections, Model routing, Voice & audio, Shortcuts, Prompts & context, Advanced, and About pages.

- [2942] The key binding is invalid.
- [2943] The key binding conflicts with another binding.
- [2944] The OS rejects the global hook.
- [2945] Input-monitoring permission is missing.
- [2946] Accessibility permission is missing.
- [2947] Another application consumes the event.

### Search Settings and jump to matching fields.

- [2948] A value required by this function is invalid.
- [2949] The settings store is read-only.
- [2950] The settings store is corrupt.
- [2951] A resource required by this function is missing.
- [2952] The pending change is discarded before persistence.
- [2953] The required application restart does not occur.

### Load a built-in configuration profile.

- [2954] A value required by this function is invalid.
- [2955] The settings store is read-only.
- [2956] The settings store is corrupt.
- [2957] A resource required by this function is missing.
- [2958] The pending change is discarded before persistence.
- [2959] The required application restart does not occur.

### Create/save a custom profile.

- [2960] The new value is empty.
- [2961] The new value is invalid.
- [2962] The new value duplicates an existing value.
- [2963] The backing store is read-only.
- [2964] The backing store is locked.
- [2965] The backing store is corrupt.
- [2966] The write is interrupted.

### Rename a custom profile.

- [2967] The new value is empty.
- [2968] The new value is invalid.
- [2969] The new value duplicates an existing value.
- [2970] The backing store is read-only.
- [2971] The backing store is locked.
- [2972] The backing store is corrupt.
- [2973] The write is interrupted.

### Delete a custom profile.

- [2974] A target required by this function is missing.
- [2975] A target required by this function is locked.
- [2976] Confirmation is cancelled.
- [2977] Required elevation is denied.
- [2978] Storage access is denied.
- [2979] Another process is using the files.
- [2980] Cleanup only partly completes.

### Run the setup check from Settings.

- [2981] A value required by this function is invalid.
- [2982] The settings store is read-only.
- [2983] The settings store is corrupt.
- [x] [2984] A resource required by this function is missing. [T002]
- [2985] The pending change is discarded before persistence.
- [2986] The required application restart does not occur.

### Run the guided profile setup from Settings.

- [2987] A value required by this function is invalid.
- [2988] The settings store is read-only.
- [2989] The settings store is corrupt.
- [2990] A resource required by this function is missing.
- [2991] The pending change is discarded before persistence.
- [2992] The required application restart does not occur.

### Save settings changes.

- [2993] The new value is empty.
- [2994] The new value is invalid.
- [2995] The new value duplicates an existing value.
- [2996] The backing store is read-only.
- [2997] The backing store is locked.
- [2998] The backing store is corrupt.
- [2999] The write is interrupted.

### Cancel/discard settings changes.

- [3000] Dirty-state tracking misses an edited control.
- [3001] A preview already changed runtime state.
- [3002] The dialog cannot restore its original snapshot.
- [3003] A child installer is still active.
- [3004] A child setup wizard is still active.

### Reset only the current Settings page.

- [3005] A value required by this function is invalid.
- [3006] The settings store is read-only.
- [3007] The settings store is corrupt.
- [3008] A resource required by this function is missing.
- [3009] The pending change is discarded before persistence.
- [3010] The required application restart does not occur.

### Reset every setting after confirmation.

- [3011] A target required by this function is missing.
- [3012] A target required by this function is locked.
- [3013] Confirmation is cancelled.
- [3014] Required elevation is denied.
- [3015] Storage access is denied.
- [3016] Another process is using the files.
- [3017] Cleanup only partly completes.

### Choose System, Light, or Dark theme.

- [3018] The selected appearance value is invalid.
- [3019] The settings store is read-only.
- [3020] The settings store is corrupt.
- [3021] A resource required by this function is missing.
- [3022] The pending change is discarded before persistence.
- [3023] The required application restart does not occur.

### Customize background, surface, text, and accent colors.

- [3024] The selected appearance value is invalid.
- [3025] The settings store is read-only.
- [3026] The settings store is corrupt.
- [3027] A resource required by this function is missing.
- [3028] The pending change is discarded before persistence.
- [3029] The required application restart does not occur.

### Customize icon size.

- [x] [3030] The configured numeric value is invalid. [T069]
- [3031] The settings store is read-only.
- [3032] The settings store is corrupt.
- [3033] A resource required by this function is missing.
- [3034] The pending change is discarded before persistence.
- [3035] The required application restart does not occur.

### Customize bubble width, line count, and font size.

- [x] [3036] The configured numeric value is invalid. [T069]
- [3037] The settings store is read-only.
- [3038] The settings store is corrupt.
- [3039] A resource required by this function is missing.
- [3040] The pending change is discarded before persistence.
- [3041] The required application restart does not occur.

### Customize bubble background, text, and spoken-word highlight colors.

- [3042] The selected appearance value is invalid.
- [3043] The settings store is read-only.
- [3044] The settings store is corrupt.
- [3045] A resource required by this function is missing.
- [3046] The pending change is discarded before persistence.
- [3047] The required application restart does not occur.

### Enable or disable bubble wheel scrolling and spoken-line snap-back.

- [3048] The configured numeric value is invalid.
- [3049] The settings store is read-only.
- [3050] The settings store is corrupt.
- [3051] A resource required by this function is missing.
- [3052] The pending change is discarded before persistence.
- [3053] The required application restart does not occur.

### Set bubble text reveal speed and held fast-forward speed.

- [x] [3054] The configured numeric value is invalid. [T069]
- [3055] The settings store is read-only.
- [3056] The settings store is corrupt.
- [3057] A resource required by this function is missing.
- [3058] The pending change is discarded before persistence.
- [3059] The required application restart does not occur.

### Set bubble display/auto-hide delay and scroll snap delay.

- [3060] The widget is hidden.
- [3061] The widget was destroyed.
- [3062] Another window consumes input.
- [3063] Saved window geometry is stale.
- [3064] The window is positioned off-screen.
- [3065] The UI thread is blocked.
- [3066] The window manager rejects the behavior.

### Choose app UI language: system, English, Chinese, Traditional Chinese, Spanish, or French.

- [3067] The selected language value is invalid.
- [3068] The settings store is read-only.
- [3069] The settings store is corrupt.
- [3070] A resource required by this function is missing.
- [3071] The pending change is discarded before persistence.
- [3072] The required application restart does not occur.

### Choose assistant reply language or match the user's language.

- [3073] The selected language value is invalid.
- [3074] The settings store is read-only.
- [3075] The settings store is corrupt.
- [3076] A resource required by this function is missing.
- [3077] The pending change is discarded before persistence.
- [3078] The required application restart does not occur.

### Edit separate Wisp, ChatGPT, and Claude system prompts.

- [3079] The conversation store is locked.
- [3080] The conversation store is corrupt.
- [3081] The selected conversation record is stale.
- [3082] A stream is still active.
- [3083] The configured model fails.
- [3084] The configured tool fails.
- [3085] Persistence is interrupted.

### Watch live output and elapsed time while an optional component installs.

- [3086] The installer process cannot start.
- [3087] The installer has no network access.
- [3088] The installer has insufficient disk space.
- [3089] The installer lacks filesystem permission.
- [3090] Installer dependency versions conflict.
- [3091] The installer returns invalid status.
- [3092] The installer is cancelled.

### Cancel a running optional installer.

- [3093] The child process ignores termination.
- [3094] The child process is stuck in an uninterruptible operation.
- [3095] Cleanup leaves partial files.

### Copy the installer log.

- [3096] The clipboard is locked.
- [3097] Focus changes before completion.
- [3098] The target blocks synthetic input.
- [3099] Another app overwrites the clipboard.
- [3100] Accessibility permission is missing.

### Open the installer log folder.

- [3101] The path no longer exists.
- [3102] Access is denied.
- [3103] The OS file-manager command is unavailable.
- [3104] The platform cannot reveal that item.

### Close the installer after it finishes.

- [3105] The installer process cannot start.
- [3106] The installer has no network access.
- [3107] The installer has insufficient disk space.
- [3108] The installer lacks filesystem permission.
- [3109] Installer dependency versions conflict.
- [3110] The installer returns invalid status.
- [3111] The installer is cancelled.

### Restart Wisp immediately when an installed component requires restart.

- [3112] The install did not become restart-ready.
- [3113] The replacement process cannot launch.
- [3114] The current process cannot shut down cleanly.

### Retain a log/status file for failed or interrupted optional installs.

- [3115] The installer process cannot start.
- [3116] The installer has no network access.
- [3117] The installer has insufficient disk space.
- [3118] The installer lacks filesystem permission.
- [3119] Installer dependency versions conflict.
- [3120] The installer returns invalid status.
- [3121] The installer is cancelled.

### Show the installed Wisp version.

- [3122] A required file is unavailable.
- [3123] Required network access is unavailable.
- [3124] A required permission is unavailable.
- [3125] Version metadata is invalid.
- [3126] Verification metadata is invalid.
- [3127] Another process locks the target.
- [3128] Disk space is low.
- [3129] Bundling cleanup fails for this function.
- [3130] Update-apply cleanup fails.

### Check GitHub Releases for a newer packaged build.

- [3131] Network access is unavailable.
- [3132] The package source is unavailable.
- [3133] Available disk space is insufficient.
- [3134] Filesystem permission is insufficient.
- [3135] Verification fails.
- [3136] Dependency versions conflict.
- [3137] The operation is cancelled.

### Download and apply a packaged update through the update flow.

- [3138] Network access is unavailable.
- [3139] The package source is unavailable.
- [3140] Available disk space is insufficient.
- [3141] Filesystem permission is insufficient.
- [3142] Verification fails.
- [3143] Dependency versions conflict.
- [3144] The operation is cancelled.

### Pull the latest `origin/main` for a supported source checkout.

- [3145] Network access is unavailable.
- [3146] The package source is unavailable.
- [3147] Available disk space is insufficient.
- [3148] Filesystem permission is insufficient.
- [3149] Verification fails.
- [3150] Dependency versions conflict.
- [3151] The operation is cancelled.

### Report update progress and errors.

- [3152] A required file is unavailable.
- [3153] Required network access is unavailable.
- [3154] A required permission is unavailable.
- [3155] Version metadata is invalid.
- [3156] Verification metadata is invalid.
- [3157] Another process locks the target.
- [3158] Disk space is low.
- [3159] Bundling cleanup fails for this function.
- [3160] Update-apply cleanup fails.

### Create a bounded, redacted crash-report ZIP.

- [3161] The selected privacy filter is unavailable.
- [3162] The selected privacy runtime is unavailable.
- [3163] Model assets are missing.
- [3164] Private-information detection times out.
- [3165] Private-information detection misclassifies content.
- [3166] The privacy review is cancelled.
- [3167] Redaction configuration is invalid.

### Reveal the created crash report in Explorer/Finder.

- [3168] The path no longer exists.
- [3169] Access is denied.
- [3170] The OS file-manager command is unavailable.
- [3171] The platform cannot reveal that item.

### Exclude chats, memory, settings, environment files, and keychain files from the crash bundle.

- [3172] A value required by this function is invalid.
- [3173] The OS keychain is unavailable.
- [3174] The endpoint URL is malformed.
- [3175] The endpoint is offline.
- [3176] The account lacks permission.
- [3177] The setting cannot be saved.

### Review the crash ZIP before sharing it.

- [3178] A required file is unavailable.
- [3179] Required network access is unavailable.
- [3180] A required permission is unavailable.
- [3181] Version metadata is invalid.
- [3182] Verification metadata is invalid.
- [3183] Another process locks the target.
- [3184] Disk space is low.
- [3185] Bundling cleanup fails for this function.
- [3186] Update-apply cleanup fails.

### Record runtime and crash logs for diagnosis.

- [3187] A required file is unavailable.
- [3188] Required network access is unavailable.
- [3189] A required permission is unavailable.
- [3190] Version metadata is invalid.
- [3191] Verification metadata is invalid.
- [3192] Another process locks the target.
- [3193] Disk space is low.
- [3194] Bundling cleanup fails for this function.
- [3195] Update-apply cleanup fails.

### Show runtime status for supported worker-host configurations.

- [3196] A required file is unavailable.
- [3197] Required network access is unavailable.
- [3198] A required permission is unavailable.
- [3199] Version metadata is invalid.
- [3200] Verification metadata is invalid.
- [3201] Another process locks the target.
- [3202] Disk space is low.
- [3203] Bundling cleanup fails for this function.
- [3204] Update-apply cleanup fails.

### Recover or recommend action after worker/model/audio failures.

- [x] [3205] The original exception is swallowed. [T071]
- [x] [3206] The original exception is misclassified. [T070]
- [3207] The worker is unresponsive.
- [3208] Available logs lack enough diagnostic context.
- [3209] No recovery rule matches the detected failure.

### Confirm the exact uninstall plan before removal.

- [3210] A required file is unavailable.
- [3211] Required network access is unavailable.
- [3212] A required permission is unavailable.
- [3213] Version metadata is invalid.
- [3214] Verification metadata is invalid.
- [3215] Another process locks the target.
- [3216] Disk space is low.
- [3217] Bundling cleanup fails for this function.
- [3218] Update-apply cleanup fails.

### Uninstall the app, its data, settings, chats, memory, add-ons, tools, logs, updates, optional packages, and Wisp-owned local AI assets.

- [3219] A target required by this function is missing.
- [3220] A target required by this function is locked.
- [3221] Confirmation is cancelled.
- [3222] Required elevation is denied.
- [3223] Storage access is denied.
- [3224] Another process is using the files.
- [3225] Cleanup only partly completes.

### Remove a source checkout only when that checkout is explicitly included in the confirmed uninstall plan.

- [3226] A target required by this function is missing.
- [3227] A target required by this function is locked.
- [3228] Confirmation is cancelled.
- [3229] Required elevation is denied.
- [3230] Storage access is denied.
- [3231] Another process is using the files.
- [3232] Cleanup only partly completes.

### Run the uninstaller as a detached self-removing process after Wisp closes.

- [3233] A target required by this function is missing.
- [3234] A target required by this function is locked.
- [3235] Confirmation is cancelled.
- [3236] Required elevation is denied.
- [3237] Storage access is denied.
- [x] [3238] Another process is using the files. [T065]
- [3239] Cleanup only partly completes.

### Register global hotkeys on Windows, macOS, and Linux through platform-specific backends.

- [x] [3240] The key binding is invalid. [T013]
- [3241] The key binding conflicts with another binding.
- [x] [3242] The OS rejects the global hook. [T014]
- [3243] Input-monitoring permission is missing.
- [3244] Accessibility permission is missing.
- [3245] Another application consumes the event.

### Read selected/focused text using native accessibility/UI APIs where available.

- [x] [3246] Nothing is selected. [T017]
- [x] [3247] Focus moved. [T018]
- [3248] The target control does not expose accessible text.
- [3249] The OS permission is missing.
- [3250] The target application is unsupported.
- [3251] The platform backend is unsupported.

### Read and restore clipboard content safely around capture/paste operations.

- [x] [3252] The clipboard is locked. [T072]
- [3253] Focus changes before completion.
- [3254] The target blocks synthetic input.
- [3255] Another app overwrites the clipboard.
- [3256] Accessibility permission is missing.

### Paste generated or dictated text back into the previously focused application.

- [3257] The clipboard is locked.
- [x] [3258] Focus changes before completion. [T016]
- [3259] The target blocks synthetic input.
- [3260] Another app overwrites the clipboard.
- [3261] Accessibility permission is missing.

### Capture screen/app regions on multi-monitor desktops.

- [3262] Screen-recording permission is missing.
- [3263] The capture backend fails.
- [3264] Monitor geometry changes during the operation.
- [3265] DPI scaling changes during the operation.
- [3266] The selected region is empty.
- [3267] The target window disappears.

### Use macOS helper/Accessibility flows where native permissions are required.

- [x] [3268] The required native backend is unavailable. [T019]
- [3269] The required desktop feature is unavailable.
- [3270] OS permissions are missing.
- [3271] Focus changes.
- [3272] The target application is unsupported.
- [x] [3273] The helper process fails. [T020]

### Use Linux AT-SPI/X11/Wayland-compatible fallbacks where available.

- [3274] The required Linux accessibility service is absent.
- [3275] The required Linux display service is absent.
- [3276] Sandbox permission denies access.
- [3277] Desktop-session permission denies access.
- [3278] The compositor blocks the operation.
- [3279] The target toolkit exposes no compatible interface.

### Use Windows native focused-control, clipboard, and hotkey integrations.

- [x] [3280] The key binding is invalid. [T013]
- [3281] The key binding conflicts with another binding.
- [x] [3282] The OS rejects the global hook. [T014]
- [3283] Input-monitoring permission is missing.
- [3284] Accessibility permission is missing.
- [3285] Another application consumes the event.

### Keep windows on screen and provide standard close/minimize controls where appropriate.

- [3286] The widget is hidden.
- [3287] The widget was destroyed.
- [3288] Another window consumes input.
- [3289] Saved window geometry is stale.
- [3290] The window is positioned off-screen.
- [3291] The UI thread is blocked.
- [3292] The window manager rejects the behavior.

### Use the operating-system file browser to reveal logs, reports, task folders, add-ons, and conversation files.

- [3293] The path no longer exists.
- [3294] Access is denied.
- [3295] The OS file-manager command is unavailable.
- [3296] The platform cannot reveal that item.

## Failure-test evidence

Directly covered failure causes: **132 / 3,296**. The remaining **3,164** causes have no direct asserting test identified in this audit.

- [T001] [tests/runtime/test_app_logging.py](../tests/runtime/test_app_logging.py)::`test_main_writes_crash_log_when_ui_worker_exits_nonzero`
- [T002] [tests/test_setup_check.py](../tests/test_setup_check.py)::`test_setup_check_warns_when_stt_package_missing`
- [T003] [tests/test_setup_check.py](../tests/test_setup_check.py)::`test_setup_check_warns_when_stt_import_fails`
- [T004] [tests/runtime/test_supervisor_ipc.py](../tests/runtime/test_supervisor_ipc.py)::`test_supervisor_shutdown_continues_after_one_worker_raises`
- [T005] [tests/runtime/test_app_logging.py](../tests/runtime/test_app_logging.py)::`test_main_shuts_down_after_nonzero_ui_exit`
- [T006] [runtime/brain/tests/test_handler_config.py](../runtime/brain/tests/test_handler_config.py)::`test_llm_test_requires_provider_and_model`
- [T007] [tests/test_llm_fallbacks.py](../tests/test_llm_fallbacks.py)::`LlmFallbackTests::test_stream_with_fallbacks_cools_down_transient_503_and_summarizes_failures`
- [T008] [tests/test_app_user_workflows.py](../tests/test_app_user_workflows.py)::`test_provider_fallback_cooldown_capability_and_auth_redaction_workflow`
- [T009] [tests/test_llm_fallbacks.py](../tests/test_llm_fallbacks.py)::`LlmFallbackTests::test_openai_compat_tools_unsupported_downgrades_to_frontloaded_context`
- [T010] [tests/test_screenshot_capability.py](../tests/test_screenshot_capability.py)::`ScreenshotCapabilityWarnings::test_model_mode_copilot_warns_unsupported`
- [T011] [tests/test_llm_fallbacks.py](../tests/test_llm_fallbacks.py)::`LlmFallbackTests::test_openrouter_route_test_reports_missing_explicit_key_before_probe`
- [T012] [tests/test_ollama_manager.py](../tests/test_ollama_manager.py)::`test_ensure_ollama_running_explains_when_not_installed`
- [T013] [tests/runtime/test_hotkey_tap_matching.py](../tests/runtime/test_hotkey_tap_matching.py)::`test_parse_combo_rejects_bare_key`
- [T014] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_start_hotkeys_surfaces_failed_registration_to_user`
- [T015] [tests/test_settings_dialog_controls.py](../tests/test_settings_dialog_controls.py)::`test_settings_reports_secondary_shortcut_conflicts_only_when_saving`
- [T016] [tests/runtime/test_native_context.py](../tests/runtime/test_native_context.py)::`test_paste_text_refuses_windows_unanchored_fallback_when_focus_token_fails`
- [T017] [tests/test_capture.py](../tests/test_capture.py)::`CaptureTests::test_uia_selection_ignores_collapsed_text_range`
- [T018] [tests/test_linux_atspi.py](../tests/test_linux_atspi.py)::`test_get_selected_text_ignores_selection_from_unfocused_app`
- [T019] [tests/test_platform_macos.py](../tests/test_platform_macos.py)::`MacPlatformTests::test_window_helpers_degrade_when_pyobjc_missing`
- [T020] [tests/test_macos_helper_ipc.py](../tests/test_macos_helper_ipc.py)::`test_calls_fail_fast_after_shutdown`
- [T021] [tests/test_snip_overlay.py](../tests/test_snip_overlay.py)::`test_tiny_drag_cancels_instead_of_selecting`
- [T022] [tests/test_context_input_safety.py](../tests/test_context_input_safety.py)::`test_oversized_image_drop_is_not_read_or_base64_encoded`
- [T023] [tests/test_context_input_safety.py](../tests/test_context_input_safety.py)::`test_oversized_image_attachment_is_not_loaded_from_disk`
- [T024] [tests/test_context_input_safety.py](../tests/test_context_input_safety.py)::`test_oversized_image_payload_is_rejected_before_decode`
- [T025] [tests/test_query_pipeline.py](../tests/test_query_pipeline.py)::`BuildContextTests::test_captured_context_has_aggregate_safety_limit`
- [T026] [tests/test_llm_fallbacks.py](../tests/test_llm_fallbacks.py)::`LlmFallbackTests::test_capture_screen_failed_precapture_does_not_fallback_to_brain_capture`
- [T027] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_stale_audio_warmup_events_cannot_overwrite_newer_notice`
- [T028] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_query_failure_reports_notice_and_returns_idle`
- [T029] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_rewrite_failure_reports_notice_and_returns_idle`
- [T030] [tests/test_app_user_workflows.py](../tests/test_app_user_workflows.py)::`test_chat_history_projects_and_corruption_recovery_workflow`
- [T031] [tests/test_external_conversation_sync.py](../tests/test_external_conversation_sync.py)::`test_push_rejects_source_outside_provider_root`
- [T032] [tests/test_external_conversation_sync.py](../tests/test_external_conversation_sync.py)::`test_export_rejects_empty_conversation`
- [T033] [tests/test_chatgpt_auth.py](../tests/test_chatgpt_auth.py)::`test_token_storage_fails_closed_when_keyring_is_unavailable`
- [T034] [tests/test_github_auth.py](../tests/test_github_auth.py)::`test_github_token_storage_fails_closed_when_keyring_is_unavailable`
- [T035] [tests/test_tts_connection.py](../tests/test_tts_connection.py)::`TtsConnectionTests::test_cartesia_connection_requires_voice_id`
- [T036] [tests/test_tts_assets.py](../tests/test_tts_assets.py)::`test_verify_flags_wrong_size_as_damaged`
- [T037] [tests/test_tts_assets.py](../tests/test_tts_assets.py)::`test_verify_reports_not_installed_when_nothing_cached`
- [T038] [tests/test_optional_deps.py](../tests/test_optional_deps.py)::`test_optional_tts_installer_classifies_windows_disk_full`
- [T039] [tests/test_optional_deps.py](../tests/test_optional_deps.py)::`test_optional_tts_installer_stt_prepare_reports_model_verification_failure`
- [T040] [tests/test_optional_deps.py](../tests/test_optional_deps.py)::`test_stt_model_probe_rejects_cpu_fallback_for_explicit_cuda`
- [T041] [tests/runtime/test_audio_host_tts.py](../tests/runtime/test_audio_host_tts.py)::`test_record_start_reports_mic_open_failure_without_raising`
- [T042] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_voice_start_does_not_show_recording_bubble_when_recording_fails`
- [T043] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_dictation_does_not_show_recording_ui_when_recording_fails`
- [T044] [tests/test_live_voice.py](../tests/test_live_voice.py)::`test_connector_failure_emits_error_then_ended`
- [T045] [tests/test_memory_quality.py](../tests/test_memory_quality.py)::`test_rejects_secrets`
- [T046] [tests/test_memory_quality.py](../tests/test_memory_quality.py)::`test_retrieve_relevant_returns_empty_for_unmatched_query`
- [T047] [runtime/brain/tests/test_handler_memory.py](../runtime/brain/tests/test_handler_memory.py)::`test_add_requires_text`
- [T048] [runtime/brain/tests/test_handler_memory.py](../runtime/brain/tests/test_handler_memory.py)::`test_update_requires_id_and_text`
- [T049] [runtime/brain/tests/test_handler_memory.py](../runtime/brain/tests/test_handler_memory.py)::`test_delete_requires_id`
- [T050] [tests/test_privacy_gateway.py](../tests/test_privacy_gateway.py)::`test_review_receives_only_scrubbed_request_and_can_cancel`
- [T051] [tests/test_privacy_gateway.py](../tests/test_privacy_gateway.py)::`test_ai_detector_failure_is_fail_closed`
- [T052] [tests/test_privacy_gateway.py](../tests/test_privacy_gateway.py)::`test_builtin_detector_rejects_invalid_card_and_iban_candidates`
- [T053] [tests/test_local_file_security.py](../tests/test_local_file_security.py)::`test_all_live_file_tools_reject_parent_root_escape`
- [T054] [tests/test_local_file_security.py](../tests/test_local_file_security.py)::`test_all_live_file_tools_reject_symlink_root_escape`
- [T055] [tests/test_builtin_model_tools.py](../tests/test_builtin_model_tools.py)::`BuiltinModelToolsTests::test_local_file_tools_execute_with_scope_and_approval`
- [T056] [tests/test_builtin_model_tools.py](../tests/test_builtin_model_tools.py)::`BuiltinModelToolsTests::test_local_file_edit_refuses_stale_approval_preview`
- [T057] [tests/test_builtin_model_tools.py](../tests/test_builtin_model_tools.py)::`BuiltinModelToolsTests::test_tool_result_budget_clips_large_outputs`
- [T058] [tests/test_addon_manager.py](../tests/test_addon_manager.py)::`test_install_addon_archive_rejects_path_traversal`
- [T059] [tests/test_addon_manager_ui.py](../tests/test_addon_manager_ui.py)::`test_install_archive_cancel_and_failure`
- [T060] [tests/test_addon_manager.py](../tests/test_addon_manager.py)::`test_approved_addon_with_missing_environment_needs_install`
- [T061] [tests/test_addon_manager.py](../tests/test_addon_manager.py)::`test_missing_permissions_deny_surfaces`
- [T062] [tests/test_agent_runner.py](../tests/test_agent_runner.py)::`ScopedWorkspaceTests::test_resolve_rejects_path_escape`
- [T063] [tests/test_agent_runner.py](../tests/test_agent_runner.py)::`AgentRunnerTests::test_llm_auth_failure_stops_instead_of_retrying`
- [T064] [tests/test_agent_runner.py](../tests/test_agent_runner.py)::`ParallelWorkRoundTests::test_same_file_write_is_leased_to_exactly_one_agent`
- [T065] [tests/test_uninstaller.py](../tests/test_uninstaller.py)::`test_standalone_worker_refuses_while_another_wisp_process_is_running`
- [T066] [tests/test_mcp_context_server.py](../tests/test_mcp_context_server.py)::`test_selected_text_empty_is_friendly`
- [T067] [tests/test_mcp_context_server.py](../tests/test_mcp_context_server.py)::`test_self_check_names_missing_dependency`
- [T068] [tests/runtime/test_flows.py](../tests/runtime/test_flows.py)::`test_read_selection_aloud_without_selection_shows_notice`
- [T069] [tests/test_env_utils.py](../tests/test_env_utils.py)::`EnvUtilsTests::test_numeric_helpers_fall_back_on_invalid_values`
- [T070] [tests/test_error_recommendations.py](../tests/test_error_recommendations.py)::`test_recommendation_for_known_error_classes`
- [T071] [tests/test_error_recommendations.py](../tests/test_error_recommendations.py)::`test_format_error_appends_recommendation_and_redacts_detail`
- [T072] [tests/test_mcp_context_server.py](../tests/test_mcp_context_server.py)::`test_clipboard_lock_fails_open_on_contention`
- [T073] [tests/test_linux_atspi.py](../tests/test_linux_atspi.py)::`test_browser_content_linux_without_url_stays_empty`
