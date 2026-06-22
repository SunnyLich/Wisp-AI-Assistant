"""Model-language templates for auto-agent presets.

These strings are selected by ASSISTANT_LANGUAGE and may be sent to the model.
They intentionally do not use the Qt UI catalogs, which follow APP_LANGUAGE.
"""
from __future__ import annotations

ROLE_RESPONSIBILITIES = {
    "Coordinator": "Break down the task, assign work, merge decisions, arbitrate conflicts, and decide when the group is done.",
    "Planner": "Turn the objective into a concrete plan, identify risks and dependencies, and hand clear next steps to the right agent.",
    "Implementer": "Make the code changes inside the selected scope, keep edits focused, run relevant checks, and report risks or blockers.",
    "Reviewer": "Inspect proposed changes, call out defects or missing tests, request fixes when needed, and approve completion when no Coordinator exists.",
    "Tester": "Design and run focused verification, reproduce failures, report exact commands and results, and confirm fixes.",
    "Researcher": "Gather read-only context before implementation: inspect files, docs, logs, APIs, and constraints, then brief the team with findings and recommendations.",
}

AGENT_ROLE_LABELS: dict[str, dict[str, str]] = {
    "English": {
        "Coordinator": "Coordinator",
        "Planner": "Planner",
        "Implementer": "Implementer",
        "Reviewer": "Reviewer",
        "Tester": "Tester",
        "Researcher": "Researcher",
    },
    "Chinese": {
        "Coordinator": "协调员",
        "Planner": "规划员",
        "Implementer": "实现者",
        "Reviewer": "审查员",
        "Tester": "测试员",
        "Researcher": "研究员",
    },
    "Chinese (Traditional)": {
        "Coordinator": "協調員",
        "Planner": "規劃員",
        "Implementer": "實作者",
        "Reviewer": "審查員",
        "Tester": "測試員",
        "Researcher": "研究員",
    },
    "Spanish": {
        "Coordinator": "Coordinador",
        "Planner": "Planificador",
        "Implementer": "Implementador",
        "Reviewer": "Revisor",
        "Tester": "Probador",
        "Researcher": "Investigador",
    },
    "French": {
        "Coordinator": "Coordinateur",
        "Planner": "Planificateur",
        "Implementer": "Implémenteur",
        "Reviewer": "Réviseur",
        "Tester": "Testeur",
        "Researcher": "Chercheur",
    },
    "German": {
        "Coordinator": "Koordinator",
        "Planner": "Planer",
        "Implementer": "Implementierer",
        "Reviewer": "Prüfer",
        "Tester": "Tester",
        "Researcher": "Rechercheur",
    },
    "Japanese": {
        "Coordinator": "調整役",
        "Planner": "計画担当",
        "Implementer": "実装担当",
        "Reviewer": "レビュー担当",
        "Tester": "テスト担当",
        "Researcher": "調査担当",
    },
    "Korean": {
        "Coordinator": "조정자",
        "Planner": "계획 담당",
        "Implementer": "구현 담당",
        "Reviewer": "검토자",
        "Tester": "테스터",
        "Researcher": "조사 담당",
    },
    "Portuguese": {
        "Coordinator": "Coordenador",
        "Planner": "Planejador",
        "Implementer": "Implementador",
        "Reviewer": "Revisor",
        "Tester": "Testador",
        "Researcher": "Pesquisador",
    },
    "Hindi": {
        "Coordinator": "समन्वयक",
        "Planner": "योजनाकार",
        "Implementer": "कार्यान्वयनकर्ता",
        "Reviewer": "समीक्षक",
        "Tester": "परीक्षक",
        "Researcher": "शोधकर्ता",
    },
}

AGENT_NAME_LABELS: dict[str, dict[str, str]] = {
    "English": {"Coordinator": "Coordinator", "Builder": "Builder", "Reviewer": "Reviewer"},
    "Chinese": {"Coordinator": "协调员", "Builder": "构建者", "Reviewer": "审查员"},
    "Chinese (Traditional)": {"Coordinator": "協調員", "Builder": "建構者", "Reviewer": "審查員"},
    "Spanish": {"Coordinator": "Coordinador", "Builder": "Constructor", "Reviewer": "Revisor"},
    "French": {"Coordinator": "Coordinateur", "Builder": "Constructeur", "Reviewer": "Réviseur"},
    "German": {"Coordinator": "Koordinator", "Builder": "Ersteller", "Reviewer": "Prüfer"},
    "Japanese": {"Coordinator": "調整役", "Builder": "ビルダー", "Reviewer": "レビュアー"},
    "Korean": {"Coordinator": "조정자", "Builder": "빌더", "Reviewer": "검토자"},
    "Portuguese": {"Coordinator": "Coordenador", "Builder": "Construtor", "Reviewer": "Revisor"},
    "Hindi": {"Coordinator": "समन्वयक", "Builder": "निर्माता", "Reviewer": "समीक्षक"},
}

AGENT_GENERIC_NAME_TEMPLATES = {
    "English": "Agent {number}",
    "Chinese": "代理 {number}",
    "Chinese (Traditional)": "代理 {number}",
    "Spanish": "Agente {number}",
    "French": "Agent {number}",
    "German": "Agent {number}",
    "Japanese": "エージェント {number}",
    "Korean": "에이전트 {number}",
    "Portuguese": "Agente {number}",
    "Hindi": "एजेंट {number}",
}

ROLE_RESPONSIBILITY_TEMPLATES: dict[str, dict[str, str]] = {
    "English": dict(ROLE_RESPONSIBILITIES),
    "Chinese": {
        "Coordinator": "拆分任务、分配工作、合并决策、协调冲突，并判断团队何时完成。",
        "Planner": "把目标转化为具体计划，识别风险和依赖，并把清晰的下一步交给合适的代理。",
        "Implementer": "在选定范围内修改代码，保持改动聚焦，运行相关检查，并报告风险或阻塞。",
        "Reviewer": "检查拟议改动，指出缺陷或缺失测试，需要时要求修复，并在没有协调员时批准完成。",
        "Tester": "设计并运行有针对性的验证，复现失败，报告准确命令和结果，并确认修复。",
        "Researcher": "在实现前收集只读上下文：检查文件、文档、日志、API 和约束，然后向团队简报发现和建议。",
    },
    "Chinese (Traditional)": {
        "Coordinator": "拆分任務、分派工作、整合決策、協調衝突，並判斷團隊何時完成。",
        "Planner": "把目標轉化為具體計畫，識別風險和依賴，並把清楚的下一步交給合適的代理。",
        "Implementer": "在選定範圍內修改程式碼，保持改動聚焦，執行相關檢查，並回報風險或阻塞。",
        "Reviewer": "檢查擬議改動，指出缺陷或缺少的測試，需要時要求修復，並在沒有協調員時批准完成。",
        "Tester": "設計並執行有針對性的驗證，重現失敗，回報確切命令和結果，並確認修復。",
        "Researcher": "在實作前收集唯讀上下文：檢查檔案、文件、日誌、API 和限制，然後向團隊簡報發現和建議。",
    },
    "Spanish": {
        "Coordinator": "Divide la tarea, asigna trabajo, integra decisiones, arbitra conflictos y decide cuándo el grupo ha terminado.",
        "Planner": "Convierte el objetivo en un plan concreto, identifica riesgos y dependencias, y entrega pasos claros al agente adecuado.",
        "Implementer": "Realiza cambios de código dentro del alcance seleccionado, mantiene las ediciones enfocadas, ejecuta comprobaciones relevantes e informa riesgos o bloqueos.",
        "Reviewer": "Inspecciona los cambios propuestos, señala defectos o pruebas faltantes, pide correcciones cuando haga falta y aprueba la finalización si no hay coordinador.",
        "Tester": "Diseña y ejecuta verificaciones enfocadas, reproduce fallos, informa comandos y resultados exactos, y confirma las correcciones.",
        "Researcher": "Reúne contexto de solo lectura antes de implementar: revisa archivos, documentos, registros, API y restricciones, y luego informa hallazgos y recomendaciones al equipo.",
    },
    "French": {
        "Coordinator": "Découpe la tâche, assigne le travail, fusionne les décisions, arbitre les conflits et décide quand le groupe a terminé.",
        "Planner": "Transforme l’objectif en plan concret, identifie les risques et dépendances, puis transmet les prochaines étapes claires au bon agent.",
        "Implementer": "Modifie le code dans le périmètre sélectionné, garde les changements ciblés, exécute les vérifications pertinentes et signale les risques ou blocages.",
        "Reviewer": "Inspecte les changements proposés, signale les défauts ou tests manquants, demande des corrections si nécessaire et approuve la fin lorsqu’il n’y a pas de coordinateur.",
        "Tester": "Conçoit et exécute des vérifications ciblées, reproduit les échecs, rapporte les commandes et résultats exacts, puis confirme les corrections.",
        "Researcher": "Rassemble du contexte en lecture seule avant l’implémentation : inspecte fichiers, documents, journaux, API et contraintes, puis informe l’équipe des conclusions et recommandations.",
    },
    "German": {
        "Coordinator": "Zerlegt die Aufgabe, weist Arbeit zu, führt Entscheidungen zusammen, schlichtet Konflikte und entscheidet, wann die Gruppe fertig ist.",
        "Planner": "Verwandelt das Ziel in einen konkreten Plan, erkennt Risiken und Abhängigkeiten und übergibt klare nächste Schritte an den passenden Agenten.",
        "Implementer": "Nimmt Codeänderungen im gewählten Bereich vor, hält Änderungen fokussiert, führt relevante Prüfungen aus und meldet Risiken oder Blockaden.",
        "Reviewer": "Prüft vorgeschlagene Änderungen, weist auf Fehler oder fehlende Tests hin, fordert bei Bedarf Korrekturen an und genehmigt den Abschluss, wenn es keinen Koordinator gibt.",
        "Tester": "Entwirft und führt gezielte Prüfungen aus, reproduziert Fehler, meldet genaue Befehle und Ergebnisse und bestätigt Korrekturen.",
        "Researcher": "Sammelt vor der Umsetzung schreibgeschützten Kontext: prüft Dateien, Dokumente, Logs, APIs und Einschränkungen und berichtet dem Team Erkenntnisse und Empfehlungen.",
    },
    "Japanese": {
        "Coordinator": "タスクを分解し、作業を割り当て、判断を統合し、衝突を調整し、チームが完了したタイミングを判断します。",
        "Planner": "目標を具体的な計画に落とし込み、リスクと依存関係を特定し、適切なエージェントに明確な次の手順を渡します。",
        "Implementer": "選択された範囲内でコードを変更し、編集を集中させ、関連する確認を実行し、リスクやブロック要因を報告します。",
        "Reviewer": "提案された変更を確認し、不具合や不足しているテストを指摘し、必要に応じて修正を依頼し、調整役がいない場合は完了を承認します。",
        "Tester": "焦点を絞った検証を設計して実行し、失敗を再現し、正確なコマンドと結果を報告し、修正を確認します。",
        "Researcher": "実装前に読み取り専用の文脈を収集します。ファイル、ドキュメント、ログ、API、制約を調べ、発見と推奨事項をチームに共有します。",
    },
    "Korean": {
        "Coordinator": "작업을 나누고, 담당을 배정하고, 결정을 통합하고, 충돌을 조정하며, 팀이 언제 완료했는지 판단합니다.",
        "Planner": "목표를 구체적인 계획으로 바꾸고, 위험과 의존성을 파악하며, 적절한 에이전트에게 명확한 다음 단계를 전달합니다.",
        "Implementer": "선택된 범위 안에서 코드 변경을 수행하고, 수정 범위를 집중적으로 유지하며, 관련 검사를 실행하고 위험이나 차단 요소를 보고합니다.",
        "Reviewer": "제안된 변경 사항을 검토하고, 결함이나 누락된 테스트를 지적하며, 필요하면 수정을 요청하고, 조정자가 없을 때 완료를 승인합니다.",
        "Tester": "초점을 맞춘 검증을 설계하고 실행하며, 실패를 재현하고, 정확한 명령과 결과를 보고하며, 수정 사항을 확인합니다.",
        "Researcher": "구현 전에 읽기 전용 컨텍스트를 수집합니다. 파일, 문서, 로그, API, 제약 조건을 살펴보고 발견 사항과 권장 사항을 팀에 공유합니다.",
    },
    "Portuguese": {
        "Coordinator": "Divide a tarefa, atribui trabalho, integra decisões, arbitra conflitos e decide quando o grupo concluiu.",
        "Planner": "Transforma o objetivo em um plano concreto, identifica riscos e dependências, e entrega próximos passos claros ao agente certo.",
        "Implementer": "Faz alterações de código dentro do escopo selecionado, mantém as edições focadas, executa verificações relevantes e relata riscos ou bloqueios.",
        "Reviewer": "Inspeciona as mudanças propostas, aponta defeitos ou testes ausentes, solicita correções quando necessário e aprova a conclusão quando não há coordenador.",
        "Tester": "Projeta e executa verificações focadas, reproduz falhas, relata comandos e resultados exatos e confirma correções.",
        "Researcher": "Reúne contexto somente leitura antes da implementação: inspeciona arquivos, documentos, logs, APIs e restrições, depois informa descobertas e recomendações à equipe.",
    },
    "Hindi": {
        "Coordinator": "कार्य को छोटे भागों में बाँटता है, काम सौंपता है, निर्णयों को मिलाता है, टकराव सुलझाता है, और तय करता है कि समूह कब पूरा हुआ।",
        "Planner": "लक्ष्य को ठोस योजना में बदलता है, जोखिम और निर्भरताएँ पहचानता है, और सही एजेंट को साफ अगले कदम देता है।",
        "Implementer": "चुने गए दायरे में कोड बदलाव करता है, संपादन केंद्रित रखता है, संबंधित जाँच चलाता है, और जोखिम या रुकावटें रिपोर्ट करता है।",
        "Reviewer": "प्रस्तावित बदलावों की जाँच करता है, दोष या छूटे परीक्षण बताता है, ज़रूरत होने पर सुधार माँगता है, और समन्वयक न होने पर पूर्णता को मंज़ूरी देता है।",
        "Tester": "केंद्रित सत्यापन बनाता और चलाता है, विफलताओं को दोहराता है, सटीक आदेश और परिणाम रिपोर्ट करता है, और सुधारों की पुष्टि करता है।",
        "Researcher": "कार्यान्वयन से पहले केवल-पढ़ने वाला संदर्भ जुटाता है: फ़ाइलें, दस्तावेज़, लॉग, API और सीमाएँ देखता है, फिर टीम को निष्कर्ष और सुझाव देता है।",
    },
}

COMMUNICATION_PHASE_LABELS: dict[str, dict[str, str]] = {
    "English": {"Planning": "Planning", "Review": "Review", "Completion": "Completion", "Status update": "Status update"},
    "Chinese": {"Planning": "规划", "Review": "审查", "Completion": "完成", "Status update": "状态更新"},
    "Chinese (Traditional)": {"Planning": "規劃", "Review": "審查", "Completion": "完成", "Status update": "狀態更新"},
    "Spanish": {"Planning": "Planificación", "Review": "Revisión", "Completion": "Finalización", "Status update": "Actualización de estado"},
    "French": {"Planning": "Planification", "Review": "Révision", "Completion": "Achèvement", "Status update": "Mise à jour d’état"},
    "German": {"Planning": "Planung", "Review": "Prüfung", "Completion": "Abschluss", "Status update": "Statusaktualisierung"},
    "Japanese": {"Planning": "計画", "Review": "レビュー", "Completion": "完了", "Status update": "状況更新"},
    "Korean": {"Planning": "계획", "Review": "검토", "Completion": "완료", "Status update": "상태 업데이트"},
    "Portuguese": {"Planning": "Planejamento", "Review": "Revisão", "Completion": "Conclusão", "Status update": "Atualização de status"},
    "Hindi": {"Planning": "योजना", "Review": "समीक्षा", "Completion": "पूर्णता", "Status update": "स्थिति अपडेट"},
}

COMMUNICATION_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "English": [
        {
            "phase": "Planning",
            "trigger": "After reading the objective and scope",
            "message": "Send the implementation plan, constraints, and first files to inspect.",
        },
        {
            "phase": "Review",
            "trigger": "After changes and local verification",
            "message": "Send changed files, verification results, and known tradeoffs for review.",
        },
        {
            "phase": "Completion",
            "trigger": "After review is complete",
            "message": "Send approval status, remaining concerns, and final-report notes.",
        },
    ],
    "Chinese": [
        {"phase": "规划", "trigger": "阅读目标和范围之后", "message": "发送实现计划、限制条件以及要先检查的文件。"},
        {"phase": "审查", "trigger": "完成改动和本地验证之后", "message": "发送已改文件、验证结果以及已知取舍供审查。"},
        {"phase": "完成", "trigger": "审查完成之后", "message": "发送批准状态、剩余疑虑以及最终报告备注。"},
    ],
    "Chinese (Traditional)": [
        {"phase": "規劃", "trigger": "閱讀目標和範圍之後", "message": "傳送實作計畫、限制條件，以及要先檢查的檔案。"},
        {"phase": "審查", "trigger": "完成改動和本地驗證之後", "message": "傳送已改檔案、驗證結果，以及已知取捨供審查。"},
        {"phase": "完成", "trigger": "審查完成之後", "message": "傳送批准狀態、剩餘疑慮，以及最終報告備註。"},
    ],
    "Spanish": [
        {"phase": "Planificación", "trigger": "Después de leer el objetivo y el alcance", "message": "Envía el plan de implementación, las restricciones y los primeros archivos que revisar."},
        {"phase": "Revisión", "trigger": "Después de los cambios y la verificación local", "message": "Envía los archivos modificados, los resultados de verificación y las compensaciones conocidas para revisión."},
        {"phase": "Finalización", "trigger": "Después de completar la revisión", "message": "Envía el estado de aprobación, las inquietudes restantes y notas para el informe final."},
    ],
    "French": [
        {"phase": "Planification", "trigger": "Après avoir lu l’objectif et le périmètre", "message": "Envoie le plan d’implémentation, les contraintes et les premiers fichiers à inspecter."},
        {"phase": "Révision", "trigger": "Après les changements et la vérification locale", "message": "Envoie les fichiers modifiés, les résultats de vérification et les compromis connus pour révision."},
        {"phase": "Achèvement", "trigger": "Après la fin de la révision", "message": "Envoie l’état d’approbation, les préoccupations restantes et les notes pour le rapport final."},
    ],
    "German": [
        {"phase": "Planung", "trigger": "Nach dem Lesen von Ziel und Umfang", "message": "Sende den Implementierungsplan, die Einschränkungen und die ersten zu prüfenden Dateien."},
        {"phase": "Prüfung", "trigger": "Nach Änderungen und lokaler Prüfung", "message": "Sende geänderte Dateien, Prüfergebnisse und bekannte Abwägungen zur Prüfung."},
        {"phase": "Abschluss", "trigger": "Nach Abschluss der Prüfung", "message": "Sende Freigabestatus, verbleibende Bedenken und Notizen für den Abschlussbericht."},
    ],
    "Japanese": [
        {"phase": "計画", "trigger": "目標と範囲を読んだ後", "message": "実装計画、制約、最初に確認するファイルを送ってください。"},
        {"phase": "レビュー", "trigger": "変更とローカル検証の後", "message": "変更したファイル、検証結果、既知のトレードオフをレビュー用に送ってください。"},
        {"phase": "完了", "trigger": "レビュー完了後", "message": "承認状況、残っている懸念、最終報告用メモを送ってください。"},
    ],
    "Korean": [
        {"phase": "계획", "trigger": "목표와 범위를 읽은 후", "message": "구현 계획, 제약 조건, 먼저 살펴볼 파일을 보내세요."},
        {"phase": "검토", "trigger": "변경과 로컬 검증 후", "message": "변경된 파일, 검증 결과, 알려진 절충점을 검토용으로 보내세요."},
        {"phase": "완료", "trigger": "검토가 완료된 후", "message": "승인 상태, 남은 우려 사항, 최종 보고서 메모를 보내세요."},
    ],
    "Portuguese": [
        {"phase": "Planejamento", "trigger": "Depois de ler o objetivo e o escopo", "message": "Envie o plano de implementação, as restrições e os primeiros arquivos a inspecionar."},
        {"phase": "Revisão", "trigger": "Depois das mudanças e da verificação local", "message": "Envie os arquivos alterados, os resultados da verificação e os tradeoffs conhecidos para revisão."},
        {"phase": "Conclusão", "trigger": "Depois que a revisão estiver completa", "message": "Envie o status de aprovação, preocupações restantes e notas para o relatório final."},
    ],
    "Hindi": [
        {"phase": "योजना", "trigger": "लक्ष्य और दायरा पढ़ने के बाद", "message": "कार्यान्वयन योजना, सीमाएँ और पहले जाँचने वाली फ़ाइलें भेजें।"},
        {"phase": "समीक्षा", "trigger": "बदलाव और स्थानीय सत्यापन के बाद", "message": "बदली गई फ़ाइलें, सत्यापन परिणाम और ज्ञात समझौते समीक्षा के लिए भेजें।"},
        {"phase": "पूर्णता", "trigger": "समीक्षा पूरी होने के बाद", "message": "अनुमोदन स्थिति, बाकी चिंताएँ और अंतिम रिपोर्ट के नोट भेजें।"},
    ],
}

AGENT_TEMPLATE_DEFAULTS = [
    ("Coordinator", "Coordinator"),
    ("Builder", "Implementer"),
    ("Reviewer", "Reviewer"),
]


def agent_template_language(language: str | None) -> str:
    """Return the agent-template language for the assistant language setting."""
    value = (language or "").strip()
    if not value or value == "match_user":
        return "English"
    lowered = value.lower()
    aliases = {
        "english": "English",
        "en": "English",
        "chinese": "Chinese",
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "zh-hans": "Chinese",
        "chinese (traditional)": "Chinese (Traditional)",
        "traditional chinese": "Chinese (Traditional)",
        "zh-hant": "Chinese (Traditional)",
        "zh_hant": "Chinese (Traditional)",
        "zh-tw": "Chinese (Traditional)",
        "zh_tw": "Chinese (Traditional)",
        "zh-hk": "Chinese (Traditional)",
        "zh_hk": "Chinese (Traditional)",
        "spanish": "Spanish",
        "es": "Spanish",
        "french": "French",
        "fr": "French",
        "german": "German",
        "de": "German",
        "japanese": "Japanese",
        "ja": "Japanese",
        "korean": "Korean",
        "ko": "Korean",
        "portuguese": "Portuguese",
        "pt": "Portuguese",
        "hindi": "Hindi",
        "hi": "Hindi",
    }
    return aliases.get(lowered, value if value in ROLE_RESPONSIBILITY_TEMPLATES else "English")


def canonical_agent_role(role: str) -> str:
    """Return the canonical built-in role for localized role labels."""
    text = " ".join(str(role or "").split())
    if not text:
        return ""
    lowered = text.lower()
    for canonical in ROLE_RESPONSIBILITIES:
        if lowered == canonical.lower():
            return canonical
    for labels in AGENT_ROLE_LABELS.values():
        for canonical, label in labels.items():
            if lowered == str(label).lower():
                return canonical
    return text


def canonical_agent_name(name: str) -> str:
    """Return the canonical built-in agent name for localized preset names."""
    text = " ".join(str(name or "").split())
    if not text:
        return ""
    lowered = text.lower()
    for canonical in ("Coordinator", "Builder", "Reviewer"):
        if lowered == canonical.lower():
            return canonical
    for labels in AGENT_NAME_LABELS.values():
        for canonical, label in labels.items():
            if lowered == str(label).lower():
                return canonical
    return text


def canonical_communication_phase(phase: str) -> str:
    """Return the canonical communication phase for localized preset phases."""
    text = " ".join(str(phase or "").split())
    if not text:
        return ""
    lowered = text.lower()
    for canonical in ("Planning", "Implementation", "Review", "Testing", "Status update", "Completion"):
        if lowered == canonical.lower():
            return canonical
    for labels in COMMUNICATION_PHASE_LABELS.values():
        for canonical, label in labels.items():
            if lowered == str(label).lower():
                return canonical
    return text


def role_label(role: str, language: str | None = None) -> str:
    """Return a localized built-in role label."""
    canonical = canonical_agent_role(role)
    lang = agent_template_language(language)
    return AGENT_ROLE_LABELS.get(lang, AGENT_ROLE_LABELS["English"]).get(canonical, str(role or ""))


def agent_name_label(name: str, language: str | None = None) -> str:
    """Return a localized built-in agent name."""
    canonical = canonical_agent_name(name)
    lang = agent_template_language(language)
    return AGENT_NAME_LABELS.get(lang, AGENT_NAME_LABELS["English"]).get(canonical, str(name or ""))


def role_responsibility(role: str, language: str | None = None) -> str:
    """Handle role responsibility for agent task spec."""
    canonical = canonical_agent_role(role)
    lang = agent_template_language(language)
    return ROLE_RESPONSIBILITY_TEMPLATES.get(lang, ROLE_RESPONSIBILITY_TEMPLATES["English"]).get(canonical, "")

def is_role_template(text: str) -> bool:
    """Return whether role template is true."""
    normalized = " ".join(text.split())
    return any(
        " ".join(value.split()) == normalized
        for templates in ROLE_RESPONSIBILITY_TEMPLATES.values()
        for value in templates.values()
    )


def _agent_field_is_builtin_default(index: int, field: str, value: str) -> bool:
    text = str(value or "")
    for language in ROLE_RESPONSIBILITY_TEMPLATES:
        rows = default_agent_specs(language)
        if index < len(rows) and text == str(rows[index].get(field, "")):
            return True
    return False


def _communication_field_is_builtin_default(index: int, field: str, value: str) -> bool:
    text = str(value or "")
    for language in ROLE_RESPONSIBILITY_TEMPLATES:
        rows = default_communication_specs(language)
        if index < len(rows) and text == str(rows[index].get(field, "")):
            return True
    return False


def default_agent_specs(language: str | None = None) -> list[dict[str, str]]:
    """Return localized default agent presets for the requested assistant language."""
    lang = agent_template_language(language)
    name_labels = AGENT_NAME_LABELS.get(lang, AGENT_NAME_LABELS["English"])
    role_labels = AGENT_ROLE_LABELS.get(lang, AGENT_ROLE_LABELS["English"])
    return [
        {
            "name": name_labels.get(name, name),
            "role": role_labels.get(role, role),
            "provider": "same as task",
            "model": "same as task",
            "responsibility": role_responsibility(role, lang),
        }
        for name, role in AGENT_TEMPLATE_DEFAULTS
    ]


def default_communication_specs(language: str | None = None) -> list[dict[str, str]]:
    """Return localized default communication presets."""
    lang = agent_template_language(language)
    agents = default_agent_specs(lang)
    templates = COMMUNICATION_TEMPLATES.get(lang, COMMUNICATION_TEMPLATES["English"])
    routes = ((0, 1), (1, 2), (2, 0))
    return [
        {
            "from_agent": agents[source_idx]["name"],
            "to_agent": agents[target_idx]["name"],
            "phase": template["phase"],
            "trigger": template["trigger"],
            "message": template["message"],
        }
        for (source_idx, target_idx), template in zip(routes, templates)
    ]


def default_generic_agent_name(number: int, language: str | None = None) -> str:
    """Return the localized generic name for a newly added agent."""
    lang = agent_template_language(language)
    return AGENT_GENERIC_NAME_TEMPLATES.get(lang, AGENT_GENERIC_NAME_TEMPLATES["English"]).format(number=number)


def localize_agent_spec_if_default(index: int, spec: dict, language: str | None = None) -> dict[str, str]:
    """Localize built-in/default agent fields while preserving custom edits."""
    defaults = default_agent_specs(language)
    if index >= len(defaults):
        return dict(spec or {})
    out = dict(spec or {})
    for field in ("name", "role", "responsibility"):
        current = str(out.get(field, ""))
        if current == "" or _agent_field_is_builtin_default(index, field, current):
            out[field] = defaults[index].get(field, current)
    out.setdefault("provider", defaults[index]["provider"])
    out.setdefault("model", defaults[index]["model"])
    return out


def localize_communication_spec_if_default(
    index: int,
    spec: dict,
    language: str | None = None,
    agent_names: list[str] | None = None,
) -> dict[str, str]:
    """Localize built-in/default communication fields while preserving custom edits."""
    defaults = default_communication_specs(language)
    if index >= len(defaults):
        return dict(spec or {})
    out = dict(spec or {})
    routes = ((0, 1), (1, 2), (2, 0))
    route = routes[index] if index < len(routes) else None
    for field in ("from_agent", "to_agent", "phase", "trigger", "message"):
        current = str(out.get(field, ""))
        if current == "" or _communication_field_is_builtin_default(index, field, current):
            if field == "from_agent" and route and agent_names and route[0] < len(agent_names):
                out[field] = agent_names[route[0]]
            elif field == "to_agent" and route and agent_names and route[1] < len(agent_names):
                out[field] = agent_names[route[1]]
            else:
                out[field] = defaults[index].get(field, current)
    return out
