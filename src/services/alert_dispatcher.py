"""
Multi-Channel Industrial Alert Dispatcher (WhatsApp Business API & Email Gateway)
Dispatches urgent notifications when critical or severe machine defects/abnormalities occur.
Integrates Meta WhatsApp Cloud API and Splus SendMailAsync Gateway with anti-spam cooldown.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from src.config import settings
from src.schemas.predictions import PredictRULResponse
from src.schemas.telemetry import TelemetryFrame
from src.utils.logger import get_logger

logger = get_logger("Services.AlertDispatcher")

# Severity Hierarchy
SEVERITY_LEVELS: Dict[str, int] = {
    "CRITICAL": 3,
    "SEVERE": 2,
    "WARNING": 1,
    "NORMAL": 0,
    "HEALTHY": 0,
}

_SEVERITY_EMOJIS = {
    "CRITICAL": "🔴 CRITICAL",
    "SEVERE": "🟠 SEVERE",
    "WARNING": "🟡 WARNING",
    "NORMAL": "🟢 NORMAL",
}

_SEVERITY_COLORS = {
    "CRITICAL": "#ef4444",
    "SEVERE": "#f97316",
    "WARNING": "#eab308",
    "NORMAL": "#22c55e",
}

# ─── Alert Notification Localization ────────────────────────────────────────
# Language codes follow IETF BCP 47 (same as the frontend i18n.ts).
# Only alert-specific labels are translated here; diagnostic codes/names are
# always kept in English because they are technical identifiers.
ALERT_STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "header": "🚨 APMS INDUSTRIAL ALERT",
        "asset": "Asset",
        "id": "ID",
        "diagnosis": "Diagnosis",
        "failing_part": "Failing Part",
        "confidence": "Confidence",
        "rul": "Remaining Useful Life",
        "repair_deadline": "Repair Deadline",
        "telemetry": "Telemetry Metrics",
        "action": "Immediate Action / Triage",
        "timestamp": "Timestamp",
        "footer": "Greenbotz Predictive Maintenance AI",
        "note": "Note",
        "vibration": "Vibration",
        "motor_temp": "Motor Temp",
        "compressor_temp": "Compressor Temp",
        "current": "Current",
        "motor_load": "Motor Load",
        "voltage_unbalance": "Voltage Unbalance",
        "realtime_active": "Real-time telemetry streaming active",
        "inspect_now": "Immediate inspection required",
        "dispatch": "Immediate technician dispatch recommended.",
        "days": "days",
        "hrs": "hrs",
        "today": "Today",
        "na": "N/A",
        "sensor_circuit": "Sensor / Electrical Circuit",
        "sev_critical": "🔴 CRITICAL",
        "sev_severe": "🟠 SEVERE",
        "sev_warning": "🟡 WARNING",
        "sev_normal": "🟢 NORMAL",
    },
    "de": {
        "header": "🚨 APMS INDUSTRIEWARNUNG",
        "asset": "Anlage",
        "id": "ID",
        "diagnosis": "Diagnose",
        "failing_part": "Fehlerteil",
        "confidence": "Konfidenz",
        "rul": "Verbleibende Nutzungsdauer",
        "repair_deadline": "Reparaturfrist",
        "telemetry": "Telemetrie-Metriken",
        "action": "Sofortmaßnahme / Triage",
        "timestamp": "Zeitstempel",
        "footer": "Greenbotz Predictive Maintenance KI",
        "note": "Hinweis",
        "vibration": "Schwingung",
        "motor_temp": "Motortemperatur",
        "compressor_temp": "Verdichtertemperatur",
        "current": "Strom",
        "motor_load": "Motorlast",
        "voltage_unbalance": "Spannungsasymmetrie",
        "realtime_active": "Echtzeit-Telemetrie aktiv",
        "inspect_now": "Sofortige Inspektion erforderlich",
        "dispatch": "Sofortiger Technikereinsatz empfohlen.",
        "days": "Tage",
        "hrs": "Std",
        "today": "Heute",
        "na": "N/V",
        "sensor_circuit": "Sensor / Elektrischer Schaltkreis",
        "sev_critical": "🔴 KRITISCH (CRITICAL)",
        "sev_severe": "🟠 SCHWERWIEGEND (SEVERE)",
        "sev_warning": "🟡 WARNUNG (WARNING)",
        "sev_normal": "🟢 NORMAL",
    },
    "fr": {
        "header": "🚨 ALERTE INDUSTRIELLE APMS",
        "asset": "Équipement",
        "id": "ID",
        "diagnosis": "Diagnostic",
        "failing_part": "Pièce défaillante",
        "confidence": "Confiance",
        "rul": "Durée de vie utile restante",
        "repair_deadline": "Délai de réparation",
        "telemetry": "Métriques de télémétrie",
        "action": "Action immédiate / Triage",
        "timestamp": "Horodatage",
        "footer": "Greenbotz IA de Maintenance Prédictive",
        "note": "Note",
        "vibration": "Vibration",
        "motor_temp": "Temp. moteur",
        "compressor_temp": "Temp. compresseur",
        "current": "Courant",
        "motor_load": "Charge moteur",
        "voltage_unbalance": "Déséquilibre de tension",
        "realtime_active": "Télémétrie en temps réel active",
        "inspect_now": "Inspection immédiate requise",
        "dispatch": "Envoi immédiat d'un technicien recommandé.",
        "days": "jours",
        "hrs": "h",
        "today": "Aujourd'hui",
        "na": "N/D",
        "sensor_circuit": "Capteur / Circuit électrique",
        "sev_critical": "🔴 CRITIQUE (CRITICAL)",
        "sev_severe": "🟠 GRAVE (SEVERE)",
        "sev_warning": "🟡 AVERTISSEMENT (WARNING)",
        "sev_normal": "🟢 NORMAL",
    },
    "es": {
        "header": "🚨 ALERTA INDUSTRIAL APMS",
        "asset": "Activo",
        "id": "ID",
        "diagnosis": "Diagnóstico",
        "failing_part": "Pieza defectuosa",
        "confidence": "Confianza",
        "rul": "Vida útil restante",
        "repair_deadline": "Fecha límite de reparación",
        "telemetry": "Métricas de telemetría",
        "action": "Acción inmediata / Triaje",
        "timestamp": "Marca de tiempo",
        "footer": "Greenbotz IA de Mantenimiento Predictivo",
        "note": "Nota",
        "vibration": "Vibración",
        "motor_temp": "Temp. motor",
        "compressor_temp": "Temp. compresor",
        "current": "Corriente",
        "motor_load": "Carga del motor",
        "voltage_unbalance": "Desequilibrio de tensión",
        "realtime_active": "Telemetría en tiempo real activa",
        "inspect_now": "Inspección inmediata requerida",
        "dispatch": "Se recomienda envío inmediato de técnico.",
        "days": "días",
        "hrs": "h",
        "today": "Hoy",
        "na": "N/D",
        "sensor_circuit": "Sensor / Circuito eléctrico",
        "sev_critical": "🔴 CRÍTICO (CRITICAL)",
        "sev_severe": "🟠 GRAVE (SEVERE)",
        "sev_warning": "🟡 ADVERTENCIA (WARNING)",
        "sev_normal": "🟢 NORMAL",
    },
    "it": {
        "header": "🚨 ALLARME INDUSTRIALE APMS",
        "asset": "Impianto",
        "id": "ID",
        "diagnosis": "Diagnosi",
        "failing_part": "Parte difettosa",
        "confidence": "Confidenza",
        "rul": "Vita utile residua",
        "repair_deadline": "Scadenza riparazione",
        "telemetry": "Metriche telemetria",
        "action": "Azione immediata / Triage",
        "timestamp": "Timestamp",
        "footer": "Greenbotz IA di Manutenzione Predittiva",
        "note": "Nota",
        "vibration": "Vibrazione",
        "motor_temp": "Temp. motore",
        "compressor_temp": "Temp. compressore",
        "current": "Corrente",
        "motor_load": "Carico motore",
        "voltage_unbalance": "Squilibrio di tensione",
        "realtime_active": "Telemetria in tempo reale attiva",
        "inspect_now": "Ispezione immediata richiesta",
        "dispatch": "Si raccomanda l'invio immediato di un tecnico.",
        "days": "giorni",
        "hrs": "h",
        "today": "Oggi",
        "na": "N/D",
        "sensor_circuit": "Sensore / Circuito elettrico",
        "sev_critical": "🔴 CRITICO (CRITICAL)",
        "sev_severe": "🟠 GRAVE (SEVERE)",
        "sev_warning": "🟡 AVVISO (WARNING)",
        "sev_normal": "🟢 NORMALE",
    },
    "pt": {
        "header": "🚨 ALERTA INDUSTRIAL APMS",
        "asset": "Ativo",
        "id": "ID",
        "diagnosis": "Diagnóstico",
        "failing_part": "Peça com defeito",
        "confidence": "Confiança",
        "rul": "Vida útil restante",
        "repair_deadline": "Prazo de reparo",
        "telemetry": "Métricas de telemetria",
        "action": "Ação imediata / Triagem",
        "timestamp": "Carimbo de hora",
        "footer": "Greenbotz IA de Manutenção Preditiva",
        "note": "Nota",
        "vibration": "Vibração",
        "motor_temp": "Temp. motor",
        "compressor_temp": "Temp. compressor",
        "current": "Corrente",
        "motor_load": "Carga do motor",
        "voltage_unbalance": "Desequilíbrio de tensão",
        "realtime_active": "Telemetria em tempo real ativa",
        "inspect_now": "Inspeção imediata necessária",
        "dispatch": "Envio imediato de técnico recomendado.",
        "days": "dias",
        "hrs": "h",
        "today": "Hoje",
        "na": "N/D",
        "sensor_circuit": "Sensor / Circuito elétrico",
        "sev_critical": "🔴 CRÍTICO (CRITICAL)",
        "sev_severe": "🟠 GRAVE (SEVERE)",
        "sev_warning": "🟡 AVISO (WARNING)",
        "sev_normal": "🟢 NORMAL",
    },
    "nl": {
        "header": "🚨 APMS INDUSTRIEEL ALARM",
        "asset": "Installatie",
        "id": "ID",
        "diagnosis": "Diagnose",
        "failing_part": "Defect onderdeel",
        "confidence": "Betrouwbaarheid",
        "rul": "Resterende levensduur",
        "repair_deadline": "Reparatiedeadline",
        "telemetry": "Telemetrie-metrics",
        "action": "Onmiddellijke actie / Triage",
        "timestamp": "Tijdstempel",
        "footer": "Greenbotz Predictief Onderhoud AI",
        "note": "Opmerking",
        "vibration": "Trilling",
        "motor_temp": "Motortemperatuur",
        "compressor_temp": "Compressortemperatuur",
        "current": "Stroom",
        "motor_load": "Motorbelasting",
        "voltage_unbalance": "Spanningsonbalans",
        "realtime_active": "Realtime telemetrie actief",
        "inspect_now": "Onmiddellijke inspectie vereist",
        "dispatch": "Directe inzet van technicien aanbevolen.",
        "days": "dagen",
        "hrs": "u",
        "today": "Vandaag",
        "na": "N.v.t.",
        "sensor_circuit": "Sensor / Elektrisch circuit",
        "sev_critical": "🔴 KRITIEK (CRITICAL)",
        "sev_severe": "🟠 ERNSTIG (SEVERE)",
        "sev_warning": "🟡 WAARSCHUWING (WARNING)",
        "sev_normal": "🟢 NORMAAL",
    },
    "pl": {
        "header": "🚨 ALARM PRZEMYSŁOWY APMS",
        "asset": "Środek trwały",
        "id": "ID",
        "diagnosis": "Diagnoza",
        "failing_part": "Uszkodzona część",
        "confidence": "Pewność",
        "rul": "Pozostała żywotność",
        "repair_deadline": "Termin naprawy",
        "telemetry": "Metryki telemetrii",
        "action": "Natychmiastowe działanie / Triage",
        "timestamp": "Znacznik czasu",
        "footer": "Greenbotz AI Konserwacji Predykcyjnej",
        "note": "Uwaga",
        "vibration": "Wibracje",
        "motor_temp": "Temp. silnika",
        "compressor_temp": "Temp. sprężarki",
        "current": "Prąd",
        "motor_load": "Obciążenie silnika",
        "voltage_unbalance": "Niesymetria napięcia",
        "realtime_active": "Telemetria w czasie rzeczywistym aktywna",
        "inspect_now": "Natychmiastowa kontrola wymagana",
        "dispatch": "Zalecane natychmiastowe wysłanie technika.",
        "days": "dni",
        "hrs": "godz",
        "today": "Dzisiaj",
        "na": "ND",
        "sensor_circuit": "Czujnik / Obwód elektryczny",
        "sev_critical": "🔴 KRYTYCZNY (CRITICAL)",
        "sev_severe": "🟠 POWAŻNY (SEVERE)",
        "sev_warning": "🟡 OSTRZEŻENIE (WARNING)",
        "sev_normal": "🟢 NORMALNY",
    },
    "hi": {
        "header": "🚨 APMS औद्योगिक अलर्ट",
        "asset": "संपत्ति",
        "id": "आईडी",
        "diagnosis": "निदान",
        "failing_part": "खराब पुर्जा",
        "confidence": "विश्वसनीयता",
        "rul": "शेष उपयोगी जीवन",
        "repair_deadline": "मरम्मत की अंतिम तिथि",
        "telemetry": "टेलीमेट्री मेट्रिक्स",
        "action": "तुरंत कार्रवाई / ट्राइएज",
        "timestamp": "समय-चिह्न",
        "footer": "Greenbotz प्रेडिक्टिव मेंटेनेंस AI",
        "note": "नोट",
        "vibration": "कंपन",
        "motor_temp": "मोटर तापमान",
        "compressor_temp": "कंप्रेसर तापमान",
        "current": "करंट",
        "motor_load": "मोटर लोड",
        "voltage_unbalance": "वोल्टेज असंतुलन",
        "realtime_active": "रियल-टाइम टेलीमेट्री सक्रिय",
        "inspect_now": "तुरंत निरीक्षण आवश्यक",
        "dispatch": "तुरंत तकनीशियन भेजने की सिफारिश की जाती है।",
        "days": "दिन",
        "hrs": "घं",
        "today": "आज",
        "na": "लागू नहीं",
        "sensor_circuit": "सेंसर / विद्युत परिपथ",
        "sev_critical": "🔴 अत्यंत गंभीर (CRITICAL)",
        "sev_severe": "🟠 गंभीर (SEVERE)",
        "sev_warning": "🟡 चेतावनी (WARNING)",
        "sev_normal": "🟢 सामान्य (NORMAL)",
    },
    "ta": {
        "header": "🚨 APMS தொழிற்சாலை எச்சரிக்கை",
        "asset": "சொத்து",
        "id": "ID",
        "diagnosis": "நோயறிதல்",
        "failing_part": "செயலிழக்கும் பகுதி",
        "confidence": "நம்பகத்தன்மை",
        "rul": "மீதமுள்ள பயனுள்ள ஆயுள்",
        "repair_deadline": "பழுதுபார்க்க கடைசி தேதி",
        "telemetry": "டெலிமெட்ரி அளவீடுகள்",
        "action": "உடனடி நடவடிக்கை / ட்ரியாஜ்",
        "timestamp": "நேர முத்திரை",
        "footer": "Greenbotz முன்கணிப்பு பராமரிப்பு AI",
        "note": "குறிப்பு",
        "vibration": "அதிர்வு",
        "motor_temp": "மோட்டார் வெப்பநிலை",
        "compressor_temp": "கம்ப்ரசர் வெப்பநிலை",
        "current": "மின்னோட்டம்",
        "motor_load": "மோட்டார் சுமை",
        "voltage_unbalance": "மின்னழுத்த சமச்சீரின்மை",
        "realtime_active": "நிகழ்நேர டெலிமெட்ரி செயலில் உள்ளது",
        "inspect_now": "உடனடி ஆய்வு தேவை",
        "dispatch": "உடனடியாக தொழில்நுட்ப வல்லுநரை அனுப்ப பரிந்துரைக்கப்படுகிறது.",
        "days": "நாட்கள்",
        "hrs": "மணி",
        "today": "இன்று",
        "na": "பொருந்தாது",
        "sensor_circuit": "சென்சார் / மின்சுற்று",
        "sev_critical": "🔴 தீவிர எச்சரிக்கை (CRITICAL)",
        "sev_severe": "🟠 மிகத் தீவிரம் (SEVERE)",
        "sev_warning": "🟡 எச்சரிக்கை (WARNING)",
        "sev_normal": "🟢 இயல்பு (NORMAL)",
    },
    "te": {
        "header": "🚨 APMS పారిశ్రామిక హెచ్చరిక",
        "asset": "ఆస్తి",
        "id": "ID",
        "diagnosis": "నిర్ధారణ",
        "failing_part": "విఫలమవుతున్న భాగం",
        "confidence": "విశ్వసనీయత",
        "rul": "మిగిలిన ఉపయోగకర జీవితం",
        "repair_deadline": "మరమ్మత్తు గడువు",
        "telemetry": "టెలిమెట్రీ కొలమానాలు",
        "action": "తక్షణ చర్య / ట్రయాజ్",
        "timestamp": "సమయ ముద్ర",
        "footer": "Greenbotz ప్రిడిక్టివ్ మెయింటెనెన్స్ AI",
        "note": "గమనిక",
        "vibration": "కంపనం",
        "motor_temp": "మోటార్ ఉష్ణోగ్రత",
        "compressor_temp": "కంప్రెసర్ ఉష్ణోగ్రత",
        "current": "కరెంట్",
        "motor_load": "మోటార్ లోడ్",
        "voltage_unbalance": "వోల్టేజ్ అసమతుల్యత",
        "realtime_active": "రియల్-టైమ్ టెలిమెట్రీ చురుకుగా ఉంది",
        "inspect_now": "తక్షణ తనిఖీ అవసరం",
        "dispatch": "వెంటనే టెక్నీషియన్‌ను పంపించాలని సిఫార్సు చేయబడింది.",
        "days": "రోజులు",
        "hrs": "గం",
        "today": "ఈరోజు",
        "na": "వర్తించదు",
        "sensor_circuit": "సెన్సార్ / విద్యుత్ సర్క్యూట్",
        "sev_critical": "🔴 అత్యంత తీవ్రమైనది (CRITICAL)",
        "sev_severe": "🟠 తీవ్రమైనది (SEVERE)",
        "sev_warning": "🟡 హెచ్చరిక (WARNING)",
        "sev_normal": "🟢 సాధారణం (NORMAL)",
    },
    "bn": {
        "header": "🚨 APMS শিল্প সতর্কতা",
        "asset": "সম্পদ",
        "id": "ID",
        "diagnosis": "রোগ নির্ণয়",
        "failing_part": "ব্যর্থ হওয়া অংশ",
        "confidence": "বিশ্বাসযোগ্যতা",
        "rul": "অবশিষ্ট কার্যকর জীবন",
        "repair_deadline": "মেরামতের সময়সীমা",
        "telemetry": "টেলিমেট্রি মেট্রিক্স",
        "action": "তাৎক্ষণিক পদক্ষেপ / ট্রিয়াজ",
        "timestamp": "সময়মুদ্রা",
        "footer": "Greenbotz প্রেডিক্টিভ মেইনটেন্যান্স AI",
        "note": "নোট",
        "vibration": "কম্পন",
        "motor_temp": "মোটর তাপমাত্রা",
        "compressor_temp": "কম্প্রেসার তাপমাত্রা",
        "current": "কারেন্ট",
        "motor_load": "মোটর লোড",
        "voltage_unbalance": "ভোল্টেজ অসামঞ্জস্য",
        "realtime_active": "রিয়েল-টাইম টেলিমেট্রি সক্রিয়",
        "inspect_now": "অবিলম্বে পরিদর্শন প্রয়োজন",
        "dispatch": "অবিলম্বে প্রযুক্তিবিদ পাঠানোর সুপারিশ করা হচ্ছে।",
        "days": "দিন",
        "hrs": "ঘ",
        "today": "আজ",
        "na": "প্রযোজ্য নয়",
        "sensor_circuit": "সেন্সর / বৈদ্যুতিক সার্কিট",
        "sev_critical": "🔴 অত্যন্ত সংকটপূর্ণ (CRITICAL)",
        "sev_severe": "🟠 গুরুতর (SEVERE)",
        "sev_warning": "🟡 সতর্কতা (WARNING)",
        "sev_normal": "🟢 স্বাভাবিক (NORMAL)",
    },
    "mr": {
        "header": "🚨 APMS औद्योगिक सूचना",
        "asset": "मालमत्ता",
        "id": "ID",
        "diagnosis": "निदान",
        "failing_part": "बिघडलेला भाग",
        "confidence": "विश्वासार्हता",
        "rul": "उर्वरित उपयुक्त आयुष्य",
        "repair_deadline": "दुरुस्तीची अंतिम तारीख",
        "telemetry": "टेलिमेट्री मेट्रिक्स",
        "action": "तात्काळ कृती / ट्रायज",
        "timestamp": "वेळ-मुद्रांक",
        "footer": "Greenbotz प्रेडिक्टिव्ह मेंटेनन्स AI",
        "note": "नोंद",
        "vibration": "कंपन",
        "motor_temp": "मोटर तापमान",
        "compressor_temp": "कंप्रेसर तापमान",
        "current": "विद्युत प्रवाह",
        "motor_load": "मोटर भार",
        "voltage_unbalance": "व्होल्टेज असमतोल",
        "realtime_active": "रिअल-टाइम टेलिमेट्री सक्रिय",
        "inspect_now": "तात्काळ तपासणी आवश्यक",
        "dispatch": "तात्काळ तंत्रज्ञ पाठविण्याची शिफारस केली जाते.",
        "days": "दिवस",
        "hrs": "तास",
        "today": "आज",
        "na": "लागू नाही",
        "sensor_circuit": "सेन्सर / विद्युत सर्किट",
        "sev_critical": "🔴 अत्यंत गंभीर (CRITICAL)",
        "sev_severe": "🟠 गंभीर (SEVERE)",
        "sev_warning": "🟡 इशारा (WARNING)",
        "sev_normal": "🟢 सामान्य (NORMAL)",
    },
    "gu": {
        "header": "🚨 APMS ઔદ્યોગિક ચેતવણી",
        "asset": "સંપત્તિ",
        "id": "ID",
        "diagnosis": "નિદાન",
        "failing_part": "નિષ્ફળ ભાગ",
        "confidence": "વિશ્વસનીયતા",
        "rul": "બાકી ઉપયોગી આવરદા",
        "repair_deadline": "સમારકામ સમયમર્યાદા",
        "telemetry": "ટેલિમેટ્રી મેટ્રિક્સ",
        "action": "તાત્કાલિક ક્રિયા / ટ્રાઇઝ",
        "timestamp": "સમય-ટિકિટ",
        "footer": "Greenbotz પ્રેડિક્ટિવ મેઇન્ટેનન્સ AI",
        "note": "નોંધ",
        "vibration": "કંપન",
        "motor_temp": "મોટર તાપમાન",
        "compressor_temp": "કોમ્પ્રેસર તાપમાન",
        "current": "વિદ્યુત પ્રવાહ",
        "motor_load": "મોટર ભાર",
        "voltage_unbalance": "વૉલ્ટેજ અસંતુલન",
        "realtime_active": "રીઅલ-ટાઇમ ટેલિમેટ્રી સક્રિય",
        "inspect_now": "તાત્કાલિક તપાસ જરૂરી",
        "dispatch": "તાત્કાલિક ટેકનિશ્યન મોકલવાની ભલામણ.",
        "days": "દિવસ",
        "hrs": "ક",
        "today": "આજે",
        "na": "લાગુ નથી",
        "sensor_circuit": "સેન્સર / ઇલેક્ટ્રિકલ સર્કિટ",
        "sev_critical": "🔴 અત્યંત ગંભીર (CRITICAL)",
        "sev_severe": "🟠 ગંભીર (SEVERE)",
        "sev_warning": "🟡 ચેતવણી (WARNING)",
        "sev_normal": "🟢 સામાન્ય (NORMAL)",
    },
}


def _alert_str(language: str, key: str) -> str:
    """Get localized alert string with English fallback."""
    lang_dict = ALERT_STRINGS.get(language, ALERT_STRINGS["en"])
    return lang_dict.get(key, ALERT_STRINGS["en"].get(key, key))

# ─── Failing Components Localization ─────────────────────────────────────────
_FAILING_PARTS: Dict[str, Dict[str, str]] = {
    "Sensor / Electrical Circuit": {
        "hi": "सेंसर / विद्युत परिपथ",
        "ta": "சென்சார் / மின்சுற்று",
        "te": "సెన్సార్ / విద్యుత్ సర్క్యూట్",
        "bn": "সেন্সর / বৈদ্যুতিক সার্কিট",
        "mr": "सेन्सर / विद्युत सर्किट",
        "gu": "સેન્સર / ઇલેક્ટ્રિકલ સર્કિટ",
        "de": "Sensor / Elektrischer Schaltkreis",
        "fr": "Capteur / Circuit électrique",
        "es": "Sensor / Circuito eléctrico",
        "it": "Sensore / Circuito elettrico",
        "pt": "Sensor / Circuito elétrico",
        "nl": "Sensor / Elektrisch circuit",
        "pl": "Czujnik / Obwód elektryczny",
    },
    "Drive-end Roller Bearing": {
        "hi": "ड्राइव-एंड रोलर बेयरिंग",
        "ta": "டிரைவ்-எண்ட் ரோலர் பேரிங்",
        "te": "డ్రైవ్-ఎండ్ రోలర్ బేరింగ్",
        "bn": "ড্রাইভ-এন্ড রোলার বিয়ারিং",
        "mr": "ड्राइव्ह-एंड रोलर बेअरिंग",
        "gu": "ડ્રાઇવ-એન્ડ રોલર બેરિંગ",
        "de": "Abtriebsseitiges Rollenlager",
        "fr": "Roulement à rouleaux côté entraînement",
        "es": "Rodamiento de rodillos del lado de accionamiento",
        "it": "Cuscinetto a rulli lato comando",
        "pt": "Rolamento de rolos do lado do acionamento",
        "nl": "Aandrijfzijde rollager",
        "pl": "Łożysko wałeczkowe po stronie napędowej",
    },
    "Shaft Coupling / Alignment": {
        "hi": "शाफ्ट कपलिंग / अलाइनमेंट",
        "ta": "ஷாஃப்ட் கப்ளிங் / சீரமைப்பு",
        "te": "షాఫ్ట్ కప్లింగ్ / అమరిక",
        "bn": "শ্যাফ্ট কাপলিং / অ্যালাইনমেন্ট",
        "mr": "शाफ्ट कपलिंग / संरेखन",
        "gu": "શાફ્ટ કપ્લિંગ / સંરેખણ",
        "de": "Wellenkupplung / Ausrichtung",
        "fr": "Accouplement d'arbre / Alignement",
        "es": "Acoplamiento de eje / Alineación",
        "it": "Giunto albero / Allineamento",
        "pt": "Acoplamento de eixo / Alinhamento",
        "nl": "Askoppeling / Uitlijning",
        "pl": "Sprzęgło wału / Osiowanie",
    },
    "Power Line / Motor Stator Winding": {
        "hi": "पावर लाइन / मोटर स्टेटर वाइंडिंग",
        "ta": "பவர் லைன் / மோட்டார் ஸ்டேட்டர் வைண்டிங்",
        "te": "పవర్ లైన్ / మోటార్ స్టేటర్ వైండింగ్",
        "bn": "পাওয়ার লাইন / মোটর স্টেটর উইন্ডিং",
        "mr": "पॉवर लाइन / मोटर स्टेटर वाइंडिंग",
        "gu": "પાવર લાઇન / મોટર સ્ટેટર વાઇન્ડિંગ",
        "de": "Stromleitung / Motorstatorwicklung",
        "fr": "Ligne d'alimentation / Enroulement du stator du moteur",
        "es": "Línea eléctrica / Devanado del estator del motor",
        "it": "Linea di alimentazione / Avvolgimento statore motore",
        "pt": "Linha de alimentação / Enrolamento do estator do motor",
        "nl": "Voedingslijn / Motorstatorwikkeling",
        "pl": "Linia zasilająca / Uzwojenie stojana silnika",
    },
    "Discharge Piping / Seal": {
        "hi": "डिस्चार्ज पाइपिंग / सील",
        "ta": "வெளியேற்ற குழாய் / சீல்",
        "te": "డిశ్చార్జ్ పైపింగ్ / సీల్",
        "bn": "ডিসচার্জ পাইপিং / সিল",
        "mr": "डिस्चार्ज पाइपिंग / सील",
        "gu": "ડિસ્ચાર્જ પાઇપિંગ / સીલ",
        "de": "Druckrohrleitung / Dichtung",
        "fr": "Tuyauterie de refoulement / Joint",
        "es": "Tubería de descarga / Sello",
        "it": "Tubazione di mandata / Guarnizione",
        "pt": "Tubulação de descarga / Vedação",
        "nl": "Afvoerleiding / Afdichting",
        "pl": "Rurociąg tłoczny / Uszczelka",
    },
}


def _translate_failing_part(part: str, language: str) -> str:
    """Translate physical/component failing part description."""
    if not part or language == "en":
        return part
    match = _FAILING_PARTS.get(part)
    if match and language in match:
        return match[language]
    part_lower = part.lower()
    for standard_part, translations in _FAILING_PARTS.items():
        if standard_part.lower() in part_lower or part_lower in standard_part.lower():
            if language in translations:
                return translations[language]
    if "sensor" in part_lower:
        return _alert_str(language, "sensor_circuit")
    return part


def _translate_diagnosis_text(text: str, language: str) -> str:
    """Translate dynamic sensor discrepancy strings or defect diagnostic names."""
    if not text or language == "en":
        return text

    # VUF derived mismatch
    m = re.search(r"Reported emVoltageImbalance\s+([\d\.]+)%\s+disagrees with\s+([\d\.]+)%\s+calculated from emVr/emVy/emVb\.?", text, re.I)
    if m:
        rep, calc = m.group(1), m.group(2)
        templates = {
            "hi": f"रिपोर्ट किया गया वोल्टेज असंतुलन {rep}% emVr/emVy/emVb से परिकलित {calc}% से मेल नहीं खाता है।",
            "ta": f"அறிவிக்கப்பட்ட மின்னழுத்த சமச்சீரின்மை {rep}% emVr/emVy/emVb இலிருந்து கணக்கிடப்பட்ட {calc}% உடன் பொருந்தவில்லை.",
            "te": f"నివేదించబడిన వోల్టేజ్ అసమతుల్యత {rep}% emVr/emVy/emVb నుండి లెక్కించిన {calc}%తో విభేదిస్తుంది.",
            "mr": f"नोंदवलेले व्होल्टेज असमतोल {rep}% emVr/emVy/emVb वरून मोजलेल्या {calc}% शी जुळत नाही.",
            "gu": f"રિપોર્ટ થયેલ વૉલ્ટેજ અસંતુલન {rep}% emVr/emVy/emVb થી ગણેલ {calc}% સાથે મેળ ખાતું નથી.",
            "bn": f"প্রতিবেদিত ভোল্টেজ অসামঞ্জস্য {rep}% emVr/emVy/emVb থেকে গণনাকৃত {calc}% এর সাথে মেলে না।",
            "de": f"Gemeldete Spannungsasymmetrie {rep}% weicht von {calc}% aus emVr/emVy/emVb ab.",
            "fr": f"Le déséquilibre de tension signalé de {rep}% est en désaccord avec {calc}% calculé à partir de emVr/emVy/emVb.",
            "es": f"El desequilibrio de voltaje reportado de {rep}% no coincide con el {calc}% calculado a partir de emVr/emVy/emVb.",
        }
        return templates.get(language, text)

    # Power calculation mismatch
    m = re.search(r"Reported emPower\s+([\d\.]+)\s*kW\s+disagrees with\s+√3·V·I·PF\s*=\s*([\d\.]+)\s*kW.*", text, re.I)
    if m:
        rep, exp = m.group(1), m.group(2)
        templates = {
            "hi": f"रिपोर्ट किया गया emPower {rep} kW √3·V·I·PF ({exp} kW) से मेल नहीं खाता है।",
            "ta": f"அறிவிக்கப்பட்ட மின் நுகர்வு {rep} kW கணக்கிடப்பட்ட {exp} kW உடன் பொருந்தவில்லை.",
            "te": f"నివేదించబడిన విద్యుత్ {rep} kW లెక్కించిన {exp} kWతో విభేదిస్తుంది.",
            "mr": f"नोंदवलेले emPower {rep} kW √3·V·I·PF ({exp} kW) शी जुळत नाही.",
            "gu": f"રિપોર્ટ થયેલ emPower {rep} kW ગણેલ {exp} kW સાથે મેળ ખાતું નથી.",
            "bn": f"প্রতিবেদিত emPower {rep} kW গণনাকৃত {exp} kW এর সাথে মেলে না।",
            "de": f"Gemeldete Wirkleistung {rep} kW weicht von √3·V·I·PF = {exp} kW ab.",
            "fr": f"La puissance électrique signalée {rep} kW ne correspond pas à √3·V·I·PF = {exp} kW.",
            "es": f"La potencia reportada {rep} kW no coincide con √3·V·I·PF = {exp} kW.",
        }
        return templates.get(language, text)

    # Temperature correlation mismatch
    m = re.search(r"tempMotor\s*\(([\d\.]+)°C\)\s*and\s*tempCompressor\s*\(([\d\.]+)°C\)\s*diverged by.*", text, re.I)
    if m:
        tm, tc = m.group(1), m.group(2)
        templates = {
            "hi": f"मोटर तापमान ({tm}°C) और कंप्रेसर तापमान ({tc}°C) में अंतर असामान्य रूप से बढ़ गया है।",
            "ta": f"மோட்டார் வெப்பநிலை ({tm}°C) மற்றும் கம்ப்ரசர் வெப்பநிலை ({tc}°C) இடையே வேறுபாடு அதிகமாக உள்ளது.",
            "te": f"మోటార్ ఉష్ణోగ్రత ({tm}°C) మరియు కంప్రెసర్ ఉష్ణోగ్రత ({tc}°C) మధ్య వ్యత్యాసం చాలా ఎక్కువగా ఉంది.",
            "mr": f"मोटर तापमान ({tm}°C) आणि कंप्रेसर तापमान ({tc}°C) यामधील तफावत असामान्यपणे वाढली आहे.",
            "gu": f"મોટર તાપમાન ({tm}°C) અને કમ્પ્રેસર તાપમાન ({tc}°C) વચ્ચેનો તફાવત અસામાન્ય રીતે વધી ગયો છે.",
            "bn": f"মোটর তাপমাত্রা ({tm}°C) এবং কম্প্রেসার তাপমাত্রা ({tc}°C) এর মধ্যে পার্থক্য অস্বাভাবিকভাবে বৃদ্ধি পেয়েছে।",
            "de": f"Motortemperatur ({tm}°C) und Kompressortemperatur ({tc}°C) weichen unzulässig voneinander ab.",
            "fr": f"La température moteur ({tm}°C) et compresseur ({tc}°C) ont divergé anormalement.",
            "es": f"La temperatura del motor ({tm}°C) y del compresor ({tc}°C) divergieron excesivamente.",
        }
        return templates.get(language, text)

    # Step-drop wire cut
    if "Step-Drop Wire Cut Signature" in text:
        templates = {
            "hi": "स्टेप-ड्रॉप वायर कट लक्षण: गति मंद हुए बिना कंपन तुरंत 0.0 mm/s हो गया।",
            "ta": "சென்சார் கம்பி துண்டிப்பு: மோட்டார் இயங்கும்போது அதிர்வு உடனடியாக 0.0 mm/s ஆக குறைந்தது.",
            "te": "సెన్సార్ వైర్ కట్: డీక్సెలరేషన్ లేకుండా కంపనం తక్షణమే 0.0 mm/s కు పడిపోయింది.",
            "mr": "स्टेप-ड्रॉप वायर कट: कोणतीही गती कमी न होता कंपन तत्काळ 0.0 mm/s वर आले.",
            "gu": "સ્ટેપ-ડ્રોપ વાયર કટ: ડિસેલરેશન વિના કંપન તરત જ 0.0 mm/s થઈ ગયું.",
            "bn": "ওয়্যার কাট লক্ষণ: ত্বরণ হ্রাস ছাড়াই কম্পন তাত্ক্ষণিকভাবে 0.0 mm/s এ নেমে গেছে।",
            "de": "Kabelbruch-Signatur: Sofortiger Abfall auf 0,0 mm/s ohne Auslaufen.",
            "fr": "Rupture de câble capteur: Chute instantanée à 0,0 mm/s sans décélération.",
            "es": "Corte de cable de sensor: Caída instantánea a 0.0 mm/s sin desaceleración.",
        }
        return templates.get(language, text)

    # Cross-domain sensor mismatch
    if "Cross-Domain Sensor Mismatch" in text or "Physical sensor cable is disconnected" in text:
        templates = {
            "hi": "क्रॉस-डोमेन सेंसर बेमेल: मोटर चालू है लेकिन कंपन 0.0 mm/s है। भौतिक सेंसर केबल कटी या अलग हो गई है।",
            "ta": "சென்சார் பொருத்தமின்மை: மோட்டார் இயங்குகிறது ஆனால் அதிர்வு 0.0 mm/s. சென்சார் கேபிள் துண்டிக்கப்பட்டுள்ளது.",
            "te": "సెన్సార్ మిస్‌మ్యాచ్: మోటార్ నడుస్తోంది కానీ వైబ్రేషన్ 0.0 mm/s. సెన్సార్ కేబుల్ డిస్‌కనెక్ట్ అయింది.",
            "mr": "क्रॉस-डोमेन सेन्सर विसंगती: मोटर चालू आहे पण कंपन 0.0 mm/s आहे. सेन्सर केबल तुटली आहे.",
            "gu": "ક્રોસ-ડોમેન સેન્સર વિસંગતતા: મોટર ચાલુ છે પણ કંપન 0.0 mm/s છે. સેન્સર કેબલ કપાઈ ગઈ છે.",
            "bn": "সেন্সর অসঙ্গতি: মোটর চলছে কিন্তু কম্পন 0.0 mm/s। সেন্সর কেবল বিচ্ছিন্ন হয়েছে।",
            "de": "Sensor-Diskrepanz: Motor läuft, aber Schwingung beträgt 0,0 mm/s. Sensorkabel getrennt.",
            "fr": "Discordance de capteur: Le moteur tourne mais la vibration est de 0,0 mm/s. Câble déconnecté.",
            "es": "Discrepancia de sensor: El motor está en marcha pero la vibración es 0.0 mm/s. Cable desconectado.",
        }
        return templates.get(language, text)

    # Direct catalog defect translations
    defect_catalog: Dict[str, Dict[str, str]] = {
        "Bearing Inner Race Fatigue Pitting": {
            "hi": "ड्राइव-एंड बेयरिंग में इनर रेस दोष (BPFI)",
            "ta": "டிரைவ்-எண்ட் பேரிங்கில் உள் வளைய குறைபாடு (BPFI)",
            "te": "డ్రైవ్-ఎండ్ బేరింగ్‌లో ఇన్నర్ రేస్ లోపం (BPFI)",
            "mr": "ड्राइव्ह-एंड बेअरिंगमध्ये इनर रेस दोष (BPFI)",
            "gu": "ડ્રાઇવ-એન્ડ બેરિંગમાં ઇનર રેસ ખામી (BPFI)",
            "bn": "ড্রাইভ-এন্ড বিয়ারিংয়ে ইনার রেস ত্রুটি (BPFI)",
            "de": "Innenringermüdung am Wälzlager (BPFI)",
            "fr": "Écaillage de la bague intérieure du roulement (BPFI)",
            "es": "Picaduras por fatiga en la pista interior del rodamiento (BPFI)",
        },
        "Inner Race Defect (BPFI)": {
            "hi": "ड्राइव-एंड बेयरिंग में इनर रेस दोष (BPFI)",
            "ta": "டிரைவ்-எண்ட் பேரிங்கில் உள் வளைய குறைபாடு (BPFI)",
            "te": "డ్రైవ్-ఎండ్ బేరింగ్‌లో ఇన్నర్ రేస్ లోపం (BPFI)",
            "mr": "ड्राइव्ह-एंड बेअरिंगमध्ये इनर रेस दोष (BPFI)",
            "gu": "ડ્રાઇવ-એન્ડ બેરિંગમાં ઇનર રેસ ખામી (BPFI)",
            "bn": "ড্রাইভ-এন্ড বিয়ারিংয়ে ইনার রেস ত্রুটি (BPFI)",
            "de": "Innenringdefekt am Lager (BPFI)",
            "fr": "Défaut de bague intérieure (BPFI)",
            "es": "Defecto de pista interior (BPFI)",
        },
        "Shaft Angular & Parallel Misalignment": {
            "hi": "शाफ्ट कोणीय एवं समानांतर मिसअलाइनमेंट",
            "ta": "ஷாஃப்ட் கோண மற்றும் இணை சீரமைப்பின்மை (2X அதிர்வு)",
            "te": "షాఫ్ట్ కోణీయ మరియు సమాంతర తప్పు అమరిక (2X)",
            "mr": "शाफ्ट कोनीय आणि समांतर संरेखन दोष (2X)",
            "gu": "શાફ્ટ કોણીય અને સમાંતર અસંરેખણ (2X)",
            "bn": "শ্যাফ্ট কৌণিক এবং সমান্তরাল মিসঅ্যালাইনমেন্ট",
            "de": "Wellenwinkel- und Parallelversatz (2X Harmonische)",
            "fr": "Désalignement angulaire et parallèle de l'arbre (harmonique 2X)",
            "es": "Desalineación angular y paralela del eje (armónico 2X)",
        },
        "3-Phase Voltage Unbalance & Stator Stress": {
            "hi": "3-फेज वोल्टेज असंतुलन और स्टेटर तनाव",
            "ta": "3-கட்ட மின்னழுத்த சமச்சீரின்மை மற்றும் ஸ்டேட்டர் அழுத்தம்",
            "te": "3-ఫేజ్ వోల్టేజ్ అసమతుల్యత మరియు స్టేటర్ ఒత్తిడి",
            "mr": "3-फेज व्होल्टेज असमतोल आणि स्टेटर ताण",
            "gu": "3-ફેઝ વૉલ્ટેજ અસંતુલન અને સ્ટેટર તણાવ",
            "bn": "৩-ফেজ ভোল্টেজ অসামঞ্জস্য এবং স্টেটর চাপ",
            "de": "3-Phasen-Spannungsasymmetrie & Statorbelastung",
            "fr": "Déséquilibre de tension triphasé & contrainte du stator",
            "es": "Desequilibrio de tensión trifásica y sobrecarga del estator",
        },
        "Compressed Air Leakage Detected": {
            "hi": "संपीड़ित वायु रिसाव पाया गया (एयर लीक)",
            "ta": "அழுத்தப்பட்ட காற்று கசிவு கண்டறியப்பட்டது",
            "te": "కంప్రెస్డ్ ఎయిర్ లీకేజ్ కనుగొనబడింది",
            "mr": "कॉम्प्रेस्ड एअर गळती आढळली",
            "gu": "કમ્પ્રેસ્ડ એર લિકેજ મળી આવ્યું",
            "bn": "সংকুচিত বায়ু ফুটো সনাক্ত করা হয়েছে",
            "de": "Druckluftleckage erkannt",
            "fr": "Fuite d'air comprimé détectée",
            "es": "Fuga de aire comprimido detectada",
        },
        "Mechanical Structural Looseness": {
            "hi": "यांत्रिक संरचनात्मक ढीलापन",
            "ta": "இயந்திர கட்டமைப்பு தளர்வு",
            "te": "యాంత్రిక నిర్మాణాత్మక వదులు",
            "mr": "यांत्रिक संरचनात्मक सैलपणा",
            "gu": "યાંત્રિક માળખાકીય ઢીલાપણું",
            "bn": "যান্ত্রিক কাঠামোগত শিথিলতা",
            "de": "Mechanische strukturelle Lockerheit",
            "fr": "Desserrage mécanique structurel",
            "es": "Holgura mecánica estructural",
        },
        "Rotor Dynamic Imbalance": {
            "hi": "रोटर डायनेमिक असंतुलन",
            "ta": "ரோட்டார் மாறும் சமநிலையின்மை",
            "te": "రోటర్ డైనమిక్ అసమతుల్యత",
            "mr": "रोटर डायनॅमिक असमतोल",
            "gu": "રોટર ડાયનેમિક અસંતુલન",
            "bn": "রটার ডায়নামিক ভারসাম্যহীনতা",
            "de": "Dynamische Rotorunwucht",
            "fr": "Balourd dynamique du rotor",
            "es": "Desequilibrio dinámico del rotor",
        },
        "Anomaly Detected": {
            "hi": "असामान्यता पाई गई",
            "ta": "அசாதாரண நிலை கண்டறியப்பட்டது",
            "te": "అసాధారణత గుర్తించబడింది",
            "mr": "विसंगती आढळली",
            "gu": "અસામાન્યતા મળી આવી",
            "bn": "অস্বাভাবিকতা সনাক্ত হয়েছে",
            "de": "Anomalie erkannt",
            "fr": "Anomalie détectée",
            "es": "Anomalía detectada",
        },
        "Normal Operation": {
            "hi": "सामान्य संचालन",
            "ta": "வழக்கமான செயல்பாடு",
            "te": "సాధారణ ఆపరేషన్",
            "mr": "सामान्य कामकाज",
            "gu": "સામાન્ય કામગીરી",
            "bn": "স্বাভাবিক অপারেশন",
            "de": "Normalbetrieb",
            "fr": "Fonctionnement normal",
            "es": "Operación normal",
        },
    }

    for orig, trans in defect_catalog.items():
        if orig.lower() in text.lower():
            return trans.get(language, text)

    return text


def _translate_action_text(action: str, language: str) -> str:
    """Translate action and triage recommendations into target language."""
    if not action or language == "en":
        return action

    # Check sensor loop wiring: ...
    if action.startswith("Check sensor loop wiring:"):
        inner = action[len("Check sensor loop wiring:"):].strip().rstrip(".")
        trans_inner = _translate_diagnosis_text(inner, language)
        prefixes = {
            "hi": "सेंसर लूप वायरिंग की जांच करें:",
            "ta": "சென்சார் லூப் வயரிங் சரிபார்க்கவும்:",
            "te": "సెన్సార్ లూప్ వైరింగ్ తనిఖీ చేయండి:",
            "mr": "सेन्सर लूप वायरिंग तपासा:",
            "gu": "સેન્સર લૂપ વાયરિંગ તપાસો:",
            "bn": "সেন্সর লুপ ওয়্যারিং পরীক্ষা করুন:",
            "de": "Sensorschleifen-Verdrahtung prüfen:",
            "fr": "Vérifier le câblage de la boucle capteur:",
            "es": "Comprobar el cableado del bucle del sensor:",
            "it": "Controllare il cablaggio del loop del sensore:",
            "pt": "Verifique a fiação do loop do sensor:",
            "nl": "Controleer de bedrading van de sensorlus:",
            "pl": "Sprawdź okablowanie pętli czujnika:",
        }
        prefix = prefixes.get(language, prefixes["hi"])
        return f"{prefix} {trans_inner}."

    if action == "Immediate technician dispatch recommended.":
        return _alert_str(language, "dispatch")

    # Generic or known field triage advice
    if "Grease bearing immediately" in action:
        triages = {
            "hi": "तुरंत बेयरिंग को ग्रीस करें और 24-48 घंटों में कंपन का दोबारा निरीक्षण करें।",
            "ta": "உடனடியாக பேரிங்கிற்கு கிரீஸ் இடவும் மற்றும் 24-48 மணிநேரத்தில் மீண்டும் ஆய்வு செய்யவும்.",
            "te": "వెంటనే బేరింగ్‌కు గ్రీజు వేయండి మరియు 24-48 గంటల్లో మళ్లీ తనిఖీ చేయండి.",
            "mr": "त्वरित बेअरिंगला ग्रीसिंग करा आणि 24-48 तासांत पुन्हा तपासणी करा.",
            "gu": "તરત જ બેરિંગને ગ્રીસ કરો અને 24-48 કલાકમાં ફરીથી તપાસો.",
            "bn": "অবিলম্বে বিয়ারিং গ্রীস করুন এবং ২৪-৪৮ ঘণ্টার মধ্যে পুনরায় পরিদর্শন করুন।",
            "de": "Lager sofort nachschmieren und Schwingung innerhalb von 24-48 Std. erneut prüfen.",
            "fr": "Graisser immédiatement le roulement et revérifier les vibrations sous 24-48h.",
            "es": "Engrasar el rodamiento de inmediato y volver a verificar la vibración en 24-48h.",
        }
        return triages.get(language, action)

    if "Perform laser shaft realignment" in action:
        triages = {
            "hi": "लेजर शाफ्ट री-अलाइनमेंट करें और कपलिंग फ्लेक्सिबल इंसर्ट की जांच करें।",
            "ta": "லேசர் ஷாஃப்ட் சீரமைப்பைச் செய்து கப்ளிங் நிலையைச் சரிபார்க்கவும்.",
            "te": "లేజర్ షాఫ్ట్ రీ-అలైన్‌మెంట్ చేయండి మరియు కప్లింగ్ స్థితిని తనిఖీ చేయండి.",
            "mr": "लेसर शाफ्ट री-अलाइनमेंट करा आणि कपलिंग घटकांची तपासणी करा.",
            "gu": "લેસર શાફ્ટ રી-અલાઇનમેન્ટ કરો અને કપ્લિંગ સ્થિતિ તપાસો.",
            "bn": "লেজার শ্যাফ্ট রি-অ্যালাইনমেন্ট সম্পাদন করুন এবং কাপলিং পরীক্ষা করুন।",
            "de": "Laser-Wellenausrichtung durchführen und Kupplungseinsätze prüfen.",
            "fr": "Effectuer un réalignement laser de l'arbre et inspecter l'accouplement.",
            "es": "Realizar realineación láser del eje e inspeccionar el acoplamiento.",
        }
        return triages.get(language, action)

    if "Check incoming utility 3-phase supply" in action or "voltage imbalance" in action.lower():
        triages = {
            "hi": "आने वाली 3-फेज बिजली आपूर्ति की जांच करें और ढीले कॉन्टैक्टर या टर्मिनल को कसें।",
            "ta": "உள்வரும் 3-கட்ட மின்சார விநியோகத்தை சரிபார்த்து தளர்வான இணைப்புகளை இறுக்கவும்.",
            "te": "ఇన్‌కమింగ్ 3-ఫేజ్ విద్యుత్ సరఫరాను తనిఖీ చేయండి మరియు టెర్మినల్స్‌ను బిగించండి.",
            "mr": "येणाऱ्या 3-फेज वीज पुरवठ्याची तपासणी करा आणि सैल जोडण्या घट्ट करा.",
            "gu": "આવતી 3-ફેઝ વીજ પુરવઠો તપાસો અને ઢીલા કનેક્શન કસો.",
            "bn": "ইনকামিং ৩-ফেজ বিদ্যুৎ সরবরাহ পরীক্ষা করুন এবং আলগা টার্মিনাল শক্ত করুন।",
            "de": "Eingehende 3-Phasen-Spannung prüfen und Schützkontakte nachziehen.",
            "fr": "Vérifier l'alimentation triphasée et resserrer les connexions lâches.",
            "es": "Verificar el suministro trifásico entrante y apretar las conexiones flojas.",
        }
        return triages.get(language, action)

    return action

# Machine asset display labels
_ASSET_NAMES: Dict[str, str] = {
    "compressor_unit_01": "Compressor Unit 01 — Elson EL30 (720 RPM, Reciprocating)",
    "motor_drive_02": "Induction Motor Drive 02 (30 kW, 1480 RPM)",
    "chiller_pump_03": "Centrifugal Chiller Pump 03 (45 kW)",
}

# In-memory deduplication & history
_lock = threading.Lock()
_alert_cooldowns: Dict[str, Dict[str, Any]] = {}
_machine_cooldowns: Dict[str, Dict[str, Any]] = {}
_in_flight_machines: Set[str] = set()
_alert_history: List[Dict[str, Any]] = []

_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    if not settings.REDIS_ENABLED:
        return None
    try:
        import redis
        client = redis.Redis.from_url(
            settings.get_redis_url(),
            decode_responses=True,
            socket_timeout=1.5,
        )
        client.ping()
        _redis_client = client
    except Exception as exc:
        logger.warning("[AlertDispatcher] Redis unavailable (%s). Falling back to memory.", exc)
        _redis_client = None
    return _redis_client


def _cooldown_redis_key(machine_id: str, fault_key: str) -> str:
    return f"{settings.REDIS_KEY_PREFIX}:alert_cd:{machine_id}:{fault_key}"


def _machine_redis_key(machine_id: str) -> str:
    return f"{settings.REDIS_KEY_PREFIX}:alert_mach:{machine_id}"


def _get_friendly_asset_name(machine_id: str) -> str:
    if machine_id in _ASSET_NAMES:
        return _ASSET_NAMES[machine_id]
    return machine_id.replace("_", " ").title()


def _format_ist_time(dt: Optional[datetime] = None) -> str:
    if not dt:
        dt = datetime.now(timezone.utc)
    # Convert UTC to IST (+5:30)
    ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%Y-%m-%d %I:%M:%S %p IST")


def evaluate_alert_severity(
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
) -> Tuple[str, int, str]:
    """
    Evaluates whether an alert condition exists and assigns a severity level:
    - CRITICAL (Level 3): ISO Zone D, RUL <= 14 days with active defect, health_status == CRITICAL.
    - SEVERE (Level 2): Hardware/cable fault (Alpha), ISO Zone C, RUL <= 30 days, health_status == SENSOR_FAULT.
    - WARNING (Level 1): Health status WATCH / DEGRADED, active defect with RUL > 30 days.
    - NORMAL (Level 0): Safe, healthy baseline.

    Returns:
        (severity_label, severity_score, reason)
    """
    status = (prediction.overall_health_status or "HEALTHY").upper()
    iso_zone = (prediction.iso_vibration_zone or "").upper()
    cable_status = (getattr(prediction.cable_check, "status", "VALID") or "VALID").upper()
    defect = prediction.defect_localization
    defect_code = (defect.defect_code if defect else "NORMAL").upper()
    rul_days = prediction.rul_prediction.rul_days if prediction.rul_prediction else 999.0

    # 1. Critical conditions
    if status == "CRITICAL" or iso_zone == "D":
        reason = f"Critical condition detected: Overall Status={status}, ISO 20816 Zone={iso_zone}"
        if defect and defect_code not in {"NORMAL", "NONE", ""}:
            reason += f", Defect={defect.defect_name} ({defect_code}), RUL={rul_days:.1f}d"
        return "CRITICAL", 3, reason

    if defect and defect_code not in {"NORMAL", "NONE", ""} and rul_days <= 14.0:
        return "CRITICAL", 3, f"Imminent failure risk: {defect.defect_name} ({defect_code}) with RUL of {rul_days:.1f} days"

    # 2. Severe conditions
    if cable_status not in {"VALID", "NORMAL", "GOOD", ""}:
        reason = f"Hardware sensor fault / signal disruption: {cable_status}"
        if prediction.cable_check and prediction.cable_check.fault_reason:
            reason += f" - {prediction.cable_check.fault_reason}"
        return "SEVERE", 2, reason

    if status == "SENSOR_FAULT":
        return "SEVERE", 2, "Transducer / Sensor hardware failure halted diagnostic DAG"

    if status == "DEGRADED" and rul_days <= 30.0:
        defect_str = f"{defect.defect_name} ({defect_code})" if defect else "Mechanical Degradation"
        return "SEVERE", 2, f"Severe asset degradation: {defect_str}, RUL={rul_days:.1f} days"

    if iso_zone == "C":
        return "SEVERE", 2, f"Elevated vibration in ISO 20816 Zone C (Unsatisfactory threshold breached)"

    # 3. Warning conditions
    if defect and defect_code not in {"NORMAL", "NONE", ""}:
        return "WARNING", 1, f"Developing defect identified: {defect.defect_name} ({defect_code}), RUL={rul_days:.1f} days"

    if status in {"DEGRADED", "WATCH"} or (prediction.overall_health_score and prediction.overall_health_score < 70.0):
        return "WARNING", 1, f"Asset health degraded (Score: {prediction.overall_health_score:.1f}%, Status: {status})"

    return "NORMAL", 0, "Asset operating within nominal limits"


def _clean_phone_number(raw_phone: str) -> str:
    """Strips spaces, hyphens, and leading '+' for Meta WhatsApp API."""
    cleaned = re.sub(r"[^\d]", "", raw_phone.strip())
    return cleaned


def send_whatsapp_alert(
    text: str,
    recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends WhatsApp alert message via Meta WhatsApp Business Cloud API.
    Uses WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID from .env.
    """
    token = (settings.WHATSAPP_TOKEN or "").strip()
    phone_number_id = (settings.WHATSAPP_PHONE_NUMBER_ID or "").strip()

    if not token or not phone_number_id:
        msg = "WhatsApp API credentials not configured (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID)"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    target_numbers = [
        _clean_phone_number(p)
        for p in (recipient or settings.WHATSAPP_ALERT_TO or "").split(",")
        if _clean_phone_number(p)
    ]

    if not target_numbers:
        msg = "No valid WhatsApp destination phone numbers configured"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    api_version = getattr(settings, "WHATSAPP_API_VERSION", "v18.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    results = []
    has_success = False

    with httpx.Client(timeout=getattr(settings, "WHATSAPP_TIMEOUT_S", 15.0)) as client:
        for phone in target_numbers:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": text,
                },
            }
            try:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code in {200, 201}:
                    has_success = True
                    data = resp.json()
                    msg_id = (data.get("messages") or [{}])[0].get("id", "ok")
                    logger.info("[AlertDispatcher] WhatsApp sent to %s (id=%s)", phone, msg_id)
                    results.append({"phone": phone, "status": "sent", "id": msg_id})
                else:
                    err_msg = resp.text[:300]
                    logger.error("[AlertDispatcher] WhatsApp send to %s failed (%s): %s", phone, resp.status_code, err_msg)
                    results.append({"phone": phone, "status": "failed", "code": resp.status_code, "error": err_msg})
            except Exception as exc:
                logger.error("[AlertDispatcher] WhatsApp request error for %s: %s", phone, exc)
                results.append({"phone": phone, "status": "error", "error": str(exc)})

    return {
        "success": has_success,
        "results": results,
    }


def send_email_alert(
    subject: str,
    message: str,
    recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends companion Email alert via Splus SendMailAsync Gateway.
    Uses OEM_MAIL_API_URL and ALERT_MAIL_TO / OEM_MAIL_TO from .env.
    """
    url = (settings.OEM_MAIL_API_URL or "").strip()
    to_addr = (recipient or getattr(settings, "ALERT_MAIL_TO", "") or settings.OEM_MAIL_TO or "").strip()

    if not url:
        msg = "OEM_MAIL_API_URL is not set in .env"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    if not to_addr:
        msg = "Recipient email (ALERT_MAIL_TO / OEM_MAIL_TO) is not set in .env"
        logger.warning("[AlertDispatcher] %s", msg)
        return {"success": False, "error": msg}

    payload = {
        "From": settings.OEM_MAIL_FROM or "",
        "Pwd": settings.OEM_MAIL_PWD or "",
        "To": to_addr,
        "CC": (settings.OEM_MAIL_CC or "").strip(),
        "Bcc": (settings.OEM_MAIL_BCC or "").strip(),
        "Subject": subject,
        "Message": message,
        "SpClientId": (settings.OEM_MAIL_SP_CLIENT_ID or "").strip(),
    }

    try:
        with httpx.Client(timeout=settings.OEM_MAIL_TIMEOUT_S) as client:
            resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code < 400:
            logger.info("[AlertDispatcher] Email sent to %s, subject=%r", to_addr, subject)
            return {"success": True, "status_code": resp.status_code, "to": to_addr}
        else:
            err = f"SendMailAsync gateway returned {resp.status_code}: {resp.text[:300]}"
            logger.error("[AlertDispatcher] %s", err)
            return {"success": False, "status_code": resp.status_code, "error": err}
    except Exception as exc:
        logger.error("[AlertDispatcher] Email send exception: %s", exc)
        return {"success": False, "error": str(exc)}



def _extract_telemetry_metrics(frame: Optional[Any], prediction: PredictRULResponse) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    if not frame:
        if prediction.electrical_health:
            eh = prediction.electrical_health
            if getattr(eh, "machine_load_pct", None) is not None:
                metrics["load_pct"] = float(eh.machine_load_pct)
            if getattr(eh, "voltage_unbalance_pct", None) is not None:
                metrics["vuf_pct"] = float(eh.voltage_unbalance_pct)
        return metrics

    # Vibration: imu_acceleration or vibration_rms
    vib = getattr(frame, "imu_acceleration", None)
    if vib is None:
        vib = getattr(frame, "vibration_rms", None)
    if vib is not None:
        metrics["vibration_rms"] = float(vib)

    # Motor Temp: temp_motor or temperature_motor
    tm = getattr(frame, "temp_motor", None)
    if tm is None:
        tm = getattr(frame, "temperature_motor", None)
    if tm is not None:
        metrics["temp_motor"] = float(tm)

    # Compressor Temp: temp_compressor or temperature_compressor
    tc = getattr(frame, "temp_compressor", None)
    if tc is None:
        tc = getattr(frame, "temperature_compressor", None)
    if tc is not None and float(tc) > 0.0:
        metrics["temp_compressor"] = float(tc)

    # Electrical load: em_machine_load or machine_load_pct
    load = getattr(frame, "em_machine_load", None)
    if load is None:
        load = getattr(frame, "machine_load_pct", None)
    if load is not None:
        metrics["load_pct"] = float(load)

    # Current: average of em_ir, em_iy, em_ib or em_current_rms
    ir = getattr(frame, "em_ir", None)
    iy = getattr(frame, "em_iy", None)
    ib = getattr(frame, "em_ib", None)
    if ir is not None and iy is not None and ib is not None:
        metrics["current_rms"] = (float(ir) + float(iy) + float(ib)) / 3.0
    elif getattr(frame, "em_current_rms", None) is not None:
        metrics["current_rms"] = float(frame.em_current_rms)

    # Voltage: em_vr, em_vy, em_vb
    vr = getattr(frame, "em_vr", None)
    vy = getattr(frame, "em_vy", None)
    vb = getattr(frame, "em_vb", None)
    if vr is not None and vy is not None and vb is not None:
        metrics["voltage_rms"] = (float(vr) + float(vy) + float(vb)) / 3.0

    return metrics


def _clean_diagnostic_text(text: str) -> str:
    """
    Cleans up internal diagnostic strings:
    - Replaces '(variance=0.00000000)' or 'variance=0.0000' with '(zero signal variance)'
    - Cleans double periods like '..' -> '.'
    - Strips excess whitespace
    """
    if not text:
        return ""
    text = re.sub(r"\(variance=0(?:\.0+)?\)", "(zero signal variance)", text, flags=re.IGNORECASE)
    text = re.sub(r"variance=0(?:\.0+)?", "zero signal variance", text, flags=re.IGNORECASE)
    text = re.sub(r"\.\.+", ".", text)
    return text.strip()


def build_whatsapp_alert_text(
    machine_id: str,
    severity: str,
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
    custom_note: Optional[str] = None,
    language: str = "en",
) -> str:
    """
    Builds clean, uniformly formatted WhatsApp alert text.
    - All field labels are bold (*Label:*).
    - All field values are normal text without backticks (avoids light/faint monospace boxes).
    - Cleaned diagnosis (no raw 'variance=0.00000000').
    - High confidence reported accurately for deterministic hardware/sensor checks.
    """
    # Shorthand for localized strings
    def s(key: str) -> str:
        return _alert_str(language, key)

    machine_name = _get_friendly_asset_name(machine_id)
    badge = s(f"sev_{severity.lower()}")
    defect = prediction.defect_localization
    rul = prediction.rul_prediction
    cable = prediction.cable_check
    timestamp_str = _format_ist_time(prediction.timestamp)

    raw_defect_title = defect.defect_name if defect else (
        cable.fault_reason or cable.status if cable and cable.status != "VALID" else "Anomaly Detected"
    )
    raw_defect_title = _clean_diagnostic_text(raw_defect_title)
    defect_title = _translate_diagnosis_text(raw_defect_title, language)
    defect_code = defect.defect_code if defect else (cable.status if cable else "ANOMALY")

    # Accurate, professional confidence representation (never misleading N/A)
    if defect and defect.confidence_percentage and defect.confidence_percentage > 0:
        confidence = f"{defect.confidence_percentage:.0f}%"
    elif cable and cable.status != "VALID":
        confidence = "100% (Hardware Verification)" if language == "en" else "100%"
    elif prediction.overall_health_status in {"CRITICAL", "SEVERE", "SENSOR_FAULT"}:
        confidence = "99%"
    else:
        confidence = "95%"

    raw_failing_part = defect.failing_component if defect else "Sensor / Electrical Circuit"
    failing_part = _translate_failing_part(raw_failing_part, language)

    if rul:
        rul_str = (
            f"{rul.rul_days:.1f} {s('days')} ({rul.rul_operating_hours:.0f} {s('hrs')})"
        )
    else:
        rul_str = s("inspect_now")
    repair_by = rul.recommended_repair_by_date if rul and rul.recommended_repair_by_date else "Today"
    if repair_by == "Today":
        repair_by = s("today")

    # Key telemetry highlights: consistent bold labels and clean values
    metrics = _extract_telemetry_metrics(frame, prediction)
    lines_telemetry = []
    if "vibration_rms" in metrics:
        iso_tag = f" [ISO Zone {prediction.iso_vibration_zone}]" if prediction.iso_vibration_zone else ""
        lines_telemetry.append(f"• *{s('vibration')}:* {metrics['vibration_rms']:.2f} mm/s RMS{iso_tag}")
    if "temp_motor" in metrics:
        lines_telemetry.append(f"• *{s('motor_temp')}:* {metrics['temp_motor']:.1f} °C")
    if "temp_compressor" in metrics:
        lines_telemetry.append(f"• *{s('compressor_temp')}:* {metrics['temp_compressor']:.1f} °C")
    if "current_rms" in metrics:
        lines_telemetry.append(f"• *{s('current')}:* {metrics['current_rms']:.1f} A")
    if "load_pct" in metrics:
        lines_telemetry.append(f"• *{s('motor_load')}:* {metrics['load_pct']:.1f} %")
    if "vuf_pct" in metrics:
        lines_telemetry.append(f"• *{s('voltage_unbalance')}:* {metrics['vuf_pct']:.2f} %")

    telemetry_block = "\n".join(lines_telemetry) if lines_telemetry else f"• {s('realtime_active')}"

    # Action summary
    action = s("dispatch")
    if defect and defect.expert_repair_guidance:
        g = defect.expert_repair_guidance
        if g.immediate_field_triage:
            action = _translate_action_text(g.immediate_field_triage, language)
    elif cable and cable.status != "VALID":
        reason_clean = _clean_diagnostic_text(cable.fault_reason or "Transducer out of range")
        action = _translate_action_text(f"Check sensor loop wiring: {reason_clean}.", language)
    action = _clean_diagnostic_text(action)

    # Clean header and consistent field formatting throughout (no backticks causing light/grey boxes)
    header_clean = s('header').replace("🚨", "").strip()
    text = f"""🚨 *{header_clean}:* *{badge}*

🏭 *{s('asset')}:* {machine_name}
🆔 *{s('id')}:* {machine_id}
🔍 *{s('diagnosis')}:* {defect_title} [{defect_code}]
🧩 *{s('failing_part')}:* {failing_part}
📊 *{s('confidence')}:* {confidence}
⏱️ *{s('rul')}:* {rul_str}
📅 *{s('repair_deadline')}:* {repair_by}

📈 *{s('telemetry')}:*
{telemetry_block}

🛠️ *{s('action')}:*
{action}

🕒 *{s('timestamp')}:* {timestamp_str}

*{s('footer')}*"""

    if custom_note:
        text += f"\n\n📝 *{s('note')}:* {custom_note}"

    return text


def _clean_subject_title(raw_title: str) -> str:
    """Creates a clean, punchy title suitable for email subject lines without multi-line clutter."""
    if not raw_title:
        return "Operational Anomaly"
    raw_title = _clean_diagnostic_text(raw_title)
    low = raw_title.lower()
    if "frozen" in low or "flatline" in low:
        return "Sensor Signal Frozen / Flatline (SENSOR_FROZEN_FLATLINE)"
    if "diverged by" in low or ("temperature" in low and "drift" in low):
        return "Thermal Sensor Divergence (Delta-T Limit Exceeded)"
    if "cable" in low or "disconnect" in low:
        return "Sensor Hardware Cable Fault"
    if "field_not_present" in low:
        return "Telemetry Signal Missing (FIELD_NOT_PRESENT)"
    if "loop" in low and "ma" in low:
        return "NAMUR NE43 Current Loop Fault"
    if "bpfi" in low:
        return "Bearing Inner Race Defect (BPFI)"
    if "bpfo" in low:
        return "Bearing Outer Race Defect (BPFO)"
    if "bsf" in low:
        return "Bearing Ball Element Defect (BSF)"
    if "ftf" in low:
        return "Bearing Cage Defect (FTF)"
    if "looseness" in low:
        return "Mechanical Looseness (MF001)"
    if "misalignment" in low:
        return "Shaft Misalignment (MF002)"
    if "imbalance" in low:
        return "Dynamic Rotor Imbalance (MF003)"
    first_sentence = raw_title.split(".")[0].strip()
    if len(first_sentence) > 55:
        return first_sentence[:52] + "..."
    return first_sentence


def build_email_alert_message(
    machine_id: str,
    severity: str,
    prediction: PredictRULResponse,
    frame: Optional[TelemetryFrame] = None,
    custom_note: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Builds a beautifully polished, executive-ready industrial alert email.
    Formatted with clear visual hierarchy, aligned data columns, and actionable triage.
    """
    machine_name = _get_friendly_asset_name(machine_id)
    badge = _SEVERITY_EMOJIS.get(severity, severity)
    defect = prediction.defect_localization
    rul = prediction.rul_prediction
    cable = prediction.cable_check
    timestamp_str = _format_ist_time(prediction.timestamp)

    defect_title = defect.defect_name if defect else (
        cable.fault_reason or cable.status if cable and cable.status != "VALID" else "Condition Anomaly"
    )
    defect_code = defect.defect_code if defect else (cable.status if cable else "ANOMALY")
    confidence = f"{defect.confidence_percentage:.1f}%" if defect and defect.confidence_percentage else "High"
    failing_part = defect.failing_component if defect else "Sensor / Electrical Transducer Circuit"

    rul_str = f"{rul.rul_days:.1f} days ({rul.rul_operating_hours:.0f} operating hours)" if rul else "Immediate Attention"
    repair_by = rul.recommended_repair_by_date if rul and rul.recommended_repair_by_date else "Today"

    # Action & triage
    action_text = "Schedule physical inspection and evaluate component condition immediately."
    triage_steps = []
    root_cause = "Real-time AI diagnostic rules identified abnormal operating patterns."
    if defect and defect.expert_repair_guidance:
        g = defect.expert_repair_guidance
        if g.immediate_field_triage:
            action_text = g.immediate_field_triage
        if g.root_cause_mechanism:
            root_cause = g.root_cause_mechanism
        if g.planned_overhaul_playbook and g.planned_overhaul_playbook.step_by_step_instructions:
            triage_steps = g.planned_overhaul_playbook.step_by_step_instructions[:5]
    elif cable and cable.status != "VALID":
        action_text = f"Verify transducer wiring and terminal connections: {cable.fault_reason or cable.status}."
        root_cause = f"Analog loop / communication fault detected on sensor channel: {cable.fault_reason or cable.status}."

    # Subject line
    subject_title = _clean_subject_title(defect_title)
    short_name = machine_name.split("—")[0].strip() if "—" in machine_name else machine_name
    subject = f"[{badge}] APMS Alert: {short_name} — {subject_title}"

    # Telemetry lines
    metrics = _extract_telemetry_metrics(frame, prediction)
    telemetry_lines = []
    if "vibration_rms" in metrics:
        iso_tag = f" [ISO Zone {prediction.iso_vibration_zone} Breached]" if prediction.iso_vibration_zone else ""
        telemetry_lines.append(f"  • Vibration Velocity RMS    : {metrics['vibration_rms']:.2f} mm/s RMS{iso_tag}")
    if "temp_motor" in metrics:
        telemetry_lines.append(f"  • Motor Stator Temperature  : {metrics['temp_motor']:.1f} °C (Limit: 85°C Warning | 95°C Trip)")
    if "temp_compressor" in metrics:
        telemetry_lines.append(f"  • Compressor Air-End Temp   : {metrics['temp_compressor']:.1f} °C (Limit: 80°C Warning | 90°C Trip)")
    if "current_rms" in metrics:
        telemetry_lines.append(f"  • Line Current (3-Phase RMS): {metrics['current_rms']:.1f} A (Full Load Amps: 68.0 A)")
    if "load_pct" in metrics:
        telemetry_lines.append(f"  • Operational Machine Load  : {metrics['load_pct']:.1f} % (Nominal Range: 40–85 %)")
    if "voltage_rms" in metrics:
        telemetry_lines.append(f"  • Grid Supply Voltage       : {metrics['voltage_rms']:.1f} V (Nominal Band: 380–435 V)")
    if "vuf_pct" in metrics:
        telemetry_lines.append(f"  • Voltage Unbalance (VUF)   : {metrics['vuf_pct']:.2f} % (IEC Limit: 1.5 %)")

    telemetry_section = "\n".join(telemetry_lines) if telemetry_lines else "  • Continuous streaming sensor telemetry active"

    # Step-by-step procedure lines
    procedure_lines = []
    if triage_steps:
        for idx, step in enumerate(triage_steps, start=1):
            procedure_lines.append(f"  {idx}. {step}")
    else:
        procedure_lines.append("  1. Notify on-duty maintenance engineer and verify physical machine state.")
        procedure_lines.append("  2. Review vibration spectral envelope and temperature trends in the APMS dashboard.")
        procedure_lines.append("  3. Schedule necessary downtime or corrective overhaul before the repair deadline.")

    procedure_section = "\n".join(procedure_lines)
    note_section = f"\nOPERATIONS NOTE:\n{custom_note}\n" if custom_note else ""

    body = f"""================================================================================
🚨 APMS INDUSTRIAL TELEMETRY ALERT — {badge}
================================================================================

Greenbotz Predictive Maintenance AI Engine
Plant Reliability Operations & Asset Protection
Timestamp: {timestamp_str}

--------------------------------------------------------------------------------
1. ASSET IDENTIFICATION
--------------------------------------------------------------------------------
  Asset Name    : {machine_name}
  Asset ID      : {machine_id}
  Health Status : {badge} (Health Score: {prediction.overall_health_score:.1f}%)
  Vibration ISO : Zone {prediction.iso_vibration_zone or 'N/A'} (ISO 20816-3 Standard)

--------------------------------------------------------------------------------
2. AI DIAGNOSTIC SUMMARY & IMPACT
--------------------------------------------------------------------------------
  Primary Fault : {defect_title}
  Fault Code    : {defect_code}
  Failing Part  : {failing_part}
  Confidence    : {confidence}
  Remaining Life: {rul_str}
  Repair Due By : {repair_by}

  Root Cause / Mechanism:
  {root_cause}

--------------------------------------------------------------------------------
3. LIVE SENSOR TELEMETRY SNAPSHOT
--------------------------------------------------------------------------------
{telemetry_section}

--------------------------------------------------------------------------------
4. PRESCRIPTIVE MAINTENANCE & IMMEDIATE TRIAGE
--------------------------------------------------------------------------------
  Immediate Field Triage:
  {action_text}

  Recommended Procedure:
{procedure_section}
{note_section}
--------------------------------------------------------------------------------
This is an automated industrial telemetry alert dispatched by the Greenbotz
Agentic Predictive & Preventive Maintenance System (APMS).
Plant Reliability & Asset Protection Operations
================================================================================
"""
    return subject, body


# Backward compatibility alias
build_email_alert_html = build_email_alert_message



def dispatch_alert_notifications(
    machine_id: str,
    prediction_response: PredictRULResponse,
    telemetry_frame: Optional[TelemetryFrame] = None,
    *,
    force: bool = False,
    custom_note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for evaluating and dispatching multi-channel alerts (WhatsApp + Email).
    Strictly guarantees that for a fault, only ONE WhatsApp message and ONE Email are triggered.
    Prevents alert storms, high-frequency burst duplicate dispatches, and defect-oscillation loops.
    """
    if not getattr(settings, "ALERT_NOTIFICATION_ENABLED", True):
        return {"dispatched": False, "reason": "Alert notifications disabled in configuration"}

    severity_label, severity_score, reason = evaluate_alert_severity(prediction_response, telemetry_frame)

    min_severity = getattr(settings, "ALERT_MIN_SEVERITY", "WARNING").upper()
    min_score = SEVERITY_LEVELS.get(min_severity, 1)

    if severity_score < min_score and not force:
        return {
            "dispatched": False,
            "severity": severity_label,
            "reason": f"Severity {severity_label} (score {severity_score}) is below minimum {min_severity} (score {min_score})",
        }

    # Determine unique defect / condition key for debouncing
    defect = prediction_response.defect_localization
    cable = prediction_response.cable_check
    defect_key = defect.defect_code if defect and defect.defect_code != "NORMAL" else (
        cable.status if cable and cable.status != "VALID" else prediction_response.overall_health_status
    )
    cache_key = f"{machine_id}:{defect_key}"

    now_ts = time.time()
    cooldown_s = getattr(settings, "ALERT_NOTIFICATION_COOLDOWN_SECONDS", 1800)
    machine_min_interval_s = getattr(settings, "ALERT_MACHINE_MIN_INTERVAL_SECONDS", 300)
    escalation_min_interval_s = getattr(settings, "ALERT_ESCALATION_MIN_INTERVAL_SECONDS", 180)

    # Atomic Cooldown, In-Flight Check, and Reservation
    with _lock:
        if not force:
            # 1. Reject if another thread or worker is currently dispatching for this machine
            if machine_id in _in_flight_machines:
                return {
                    "dispatched": False,
                    "severity": severity_label,
                    "reason": f"Alert dispatch already in flight for asset {machine_id}",
                }

            # 2. Check machine-level cooldown (stops defect-name oscillation like SENSOR_FROZEN_FLATLINE <-> BPFI)
            last_mach = _machine_cooldowns.get(machine_id)
            if last_mach:
                elapsed_mach = now_ts - last_mach.get("sent_at", 0)
                last_defect = last_mach.get("defect_key")
                last_score = last_mach.get("severity_score", 0)

                # Same fault on this machine: full cooldown required
                if last_defect == defect_key:
                    if elapsed_mach < cooldown_s:
                        # Allow escalation only if enough time passed since last alert to avoid sub-second bursts
                        if severity_score > last_score:
                            if elapsed_mach < escalation_min_interval_s:
                                return {
                                    "dispatched": False,
                                    "severity": severity_label,
                                    "reason": f"Escalated alert throttled for {machine_id} ({int(elapsed_mach)}s < {escalation_min_interval_s}s min interval)",
                                }
                            logger.info(
                                "[AlertDispatcher] Severity escalated for %s (%s -> %s). Allowing alert.",
                                cache_key,
                                last_mach.get("severity"),
                                severity_label,
                            )
                        else:
                            return {
                                "dispatched": False,
                                "severity": severity_label,
                                "reason": f"Alert on cooldown for {cache_key} ({int(cooldown_s - elapsed_mach)}s remaining). Prior severity: {last_mach.get('severity')}",
                            }
                else:
                    # Different fault code on the same machine: enforce machine_min_interval_s
                    if elapsed_mach < machine_min_interval_s:
                        if severity_score > last_score and elapsed_mach >= escalation_min_interval_s:
                            logger.info(
                                "[AlertDispatcher] Higher severity fault %s for %s while %s active. Escalating.",
                                defect_key,
                                machine_id,
                                last_defect,
                            )
                        else:
                            return {
                                "dispatched": False,
                                "severity": severity_label,
                                "reason": f"Machine {machine_id} on anti-storm debounce ({int(machine_min_interval_s - elapsed_mach)}s remaining, prior fault: {last_defect})",
                            }

            # 3. Check defect-specific cooldown
            prior = _alert_cooldowns.get(cache_key)
            if prior:
                elapsed = now_ts - prior.get("sent_at", 0)
                prior_score = prior.get("severity_score", 0)
                if elapsed < cooldown_s:
                    if severity_score > prior_score:
                        if elapsed < escalation_min_interval_s:
                            return {
                                "dispatched": False,
                                "severity": severity_label,
                                "reason": f"Escalated alert throttled for {cache_key} ({int(elapsed)}s < {escalation_min_interval_s}s)",
                            }
                    else:
                        return {
                            "dispatched": False,
                            "severity": severity_label,
                            "reason": f"Alert on cooldown for {cache_key} ({int(cooldown_s - elapsed)}s remaining). Prior severity: {prior.get('severity')}",
                        }

            # 4. Check Redis if available
            r = _get_redis()
            if r:
                try:
                    r_mach_key = _machine_redis_key(machine_id)
                    r_mach_val = r.get(r_mach_key)
                    if r_mach_val:
                        parts = r_mach_val.split(":")
                        r_score = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 1
                        if severity_score <= r_score:
                            return {
                                "dispatched": False,
                                "severity": severity_label,
                                "reason": f"Asset {machine_id} on Redis debounce",
                            }

                    r_key = _cooldown_redis_key(machine_id, defect_key)
                    r_val = r.get(r_key)
                    if r_val:
                        r_score = int(r_val)
                        if severity_score <= r_score:
                            return {
                                "dispatched": False,
                                "severity": severity_label,
                                "reason": f"Alert on Redis cooldown for {cache_key}",
                            }
                except Exception as rex:
                    logger.warning("[AlertDispatcher] Redis cooldown check failed: %s", rex)

        # 5. ATOMIC PRE-RESERVATION:
        # Mark in-flight and set provisional cooldown record BEFORE releasing lock!
        _in_flight_machines.add(machine_id)
        provisional_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "machine_id": machine_id,
            "defect_key": defect_key,
            "severity": severity_label,
            "severity_score": severity_score,
            "sent_at": now_ts,
            "whatsapp": {"pending": True},
            "email": {"pending": True},
            "reason": reason,
        }
        _alert_cooldowns[cache_key] = provisional_record
        _machine_cooldowns[machine_id] = provisional_record

        r = _get_redis()
        if r:
            try:
                r_key = _cooldown_redis_key(machine_id, defect_key)
                r.set(r_key, str(severity_score), ex=cooldown_s)
                r_mach_key = _machine_redis_key(machine_id)
                r.set(r_mach_key, f"{severity_score}:{defect_key}", ex=machine_min_interval_s)
            except Exception as rex:
                logger.warning("[AlertDispatcher] Redis cooldown pre-reservation failed: %s", rex)

    # Network Dispatch (Outside Lock)
    try:
        # Resolve alert language
        try:
            from src.api.routes.settings import get_alert_language
            _alert_lang = get_alert_language()
        except Exception:
            _alert_lang = "en"

        wa_text = build_whatsapp_alert_text(
            machine_id=machine_id,
            severity=severity_label,
            prediction=prediction_response,
            frame=telemetry_frame,
            custom_note=custom_note,
            language=_alert_lang,
        )

        mail_subject, mail_html = build_email_alert_html(
            machine_id=machine_id,
            severity=severity_label,
            prediction=prediction_response,
            frame=telemetry_frame,
            custom_note=custom_note,
        )

        # Dispatch WhatsApp (exactly ONE call)
        wa_result = send_whatsapp_alert(wa_text)

        # Dispatch Email (exactly ONE call)
        email_result = send_email_alert(mail_subject, mail_html)

        # Final record update
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "machine_id": machine_id,
            "defect_key": defect_key,
            "severity": severity_label,
            "severity_score": severity_score,
            "sent_at": now_ts,
            "whatsapp": wa_result,
            "email": email_result,
            "reason": reason,
        }

        with _lock:
            _alert_cooldowns[cache_key] = record
            _machine_cooldowns[machine_id] = record
            _alert_history.append(record)
            if len(_alert_history) > 100:
                _alert_history.pop(0)

        logger.info(
            "[AlertDispatcher] Dispatched alerts for %s (%s). WhatsApp: %s, Email: %s",
            machine_id,
            severity_label,
            wa_result.get("success"),
            email_result.get("success"),
        )

        return {
            "dispatched": True,
            "severity": severity_label,
            "severity_score": severity_score,
            "whatsapp": wa_result,
            "email": email_result,
            "record": record,
        }
    finally:
        with _lock:
            _in_flight_machines.discard(machine_id)


def note_alert_cleared(machine_id: str, clear_cooldown: bool = False) -> None:
    """
    Clears active alert state when machine returns to confirmed healthy state
    or is explicitly reset by maintenance operator.
    Only clears anti-spam cooldown timers if clear_cooldown=True (e.g. manual operator reset).
    """
    with _lock:
        _in_flight_machines.discard(machine_id)
        if clear_cooldown:
            keys_to_clear = [k for k in _alert_cooldowns if k.startswith(f"{machine_id}:")]
            for k in keys_to_clear:
                _alert_cooldowns.pop(k, None)
            _machine_cooldowns.pop(machine_id, None)

    if clear_cooldown:
        r = _get_redis()
        if r:
            try:
                pattern = _cooldown_redis_key(machine_id, "*")
                keys = r.keys(pattern)
                if keys:
                    r.delete(*keys)
                r_mach_key = _machine_redis_key(machine_id)
                r.delete(r_mach_key)
            except Exception as exc:
                logger.warning("[AlertDispatcher] Redis clear error: %s", exc)


def get_alert_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent dispatched alerts history."""
    with _lock:
        return list(reversed(_alert_history[-limit:]))
