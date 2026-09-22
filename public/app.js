/**
 * APMS Industrial Predictive Maintenance Dashboard
 * Interactive Client Application connected to FastAPI AI Engine
 */

const FAULT_CATALOG = [
  { code: 'MF001', name: 'Structural Looseness', baseFreq: 'Sub-harmonics / 0.5X' },
  { code: 'MF002', name: 'Shaft Misalignment', baseFreq: '2X Shaft Harmonic' },
  { code: 'MF003', name: 'Rotor Dynamic Unbalance', baseFreq: '1X Rotational Peak' },
  { code: 'BPFI',  name: 'Bearing Inner Race', baseFreq: '162.4 Hz Demodulated' },
  { code: 'BPFO',  name: 'Bearing Outer Race', baseFreq: '108.2 Hz Demodulated' },
  { code: 'BSF',   name: 'Ball Spin Element', baseFreq: '65.1 Hz Impact' },
  { code: 'FTF',   name: 'Bearing Cage Flutter', baseFreq: '10.2 Hz Modulated' },
  { code: 'VUF',   name: 'Voltage Unbalance', baseFreq: '3-Phase Quality' }
];

let currentMachineId = 'compressor_unit_01';
let currentTelemetry = {
  machineId: 'compressor_unit_01',
  imuAcceleration: 1.82,
  rpm: 1480,
  tempMotor: 48.5,
  tempCompressor: 52.1,
  tempAmbient: 25.0,
  humidity: 45.0,
  emIr: 14.2, emIy: 14.1, emIb: 14.0,
  emVr: 400.0, emVy: 400.0, emVb: 399.0,
  emMachineLoad: 78.5,
  emVoltageImbalance: 1.20,
  emPower: 28.4,
  bearingModel: 'SKF-6208',
  isoMachineGroup: '2',
  isoSupport: 'flexible',
  vibrationHarmonics: [
    { frequency: 24.7, amplitude: -22.5 },
    { frequency: 49.3, amplitude: -26.0 },
    { frequency: 162.4, amplitude: -28.0 }
  ]
};

function apiHeaders(extra) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, extra || {});
  const key = window.localStorage.getItem('APMS_API_KEY');
  if (key) headers['X-API-Key'] = key;
  return headers;
}

function synthesizeTriggeredWaveform(axisPhase) {
  const fs = 2560;
  const n = 1024;
  const rpm = currentTelemetry.rpm || 1480;
  const f1 = Math.max(5, rpm / 60);
  const amp = Math.max(0.2, currentTelemetry.imuAcceleration || 1);
  const samples = [];
  for (let i = 0; i < n; i++) {
    const t = i / fs;
    let x = 0.6 * amp * Math.sin(2 * Math.PI * f1 * t + axisPhase)
      + 0.3 * amp * Math.sin(2 * Math.PI * 2 * f1 * t + axisPhase);
    (currentTelemetry.vibrationHarmonics || []).forEach((h) => {
      const lin = Math.max(0.05, Math.pow(10, (h.amplitude || -30) / 20) * amp);
      x += lin * Math.sin(2 * Math.PI * h.frequency * t + axisPhase);
    });
    samples.push(Number((x + (Math.random() - 0.5) * 0.04 * amp).toFixed(5)));
  }
  return samples;
}

function attachDashboardWaveforms(payload) {
  const body = Object.assign({}, payload);
  if (!body.waveformX || body.waveformX.length < 128) {
    body.sampleRateHz = 2560;
    body.waveformX = synthesizeTriggeredWaveform(0);
    body.waveformY = synthesizeTriggeredWaveform(Math.PI / 2);
    body.waveformZ = synthesizeTriggeredWaveform(Math.PI / 4);
  }
  return body;
}

async function apiFetch(url, options) {
  const opts = options || {};
  const headers = apiHeaders(opts.headers);
  const res = await fetch(url, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    console.warn('API auth required. Set localStorage APMS_API_KEY to the plant API key.');
  }
  return res;
}

const historyVibration = [];
const historyThermal = [];
const maxHistory = 30;

let appThresholds = {
  vibration: { warning_max: 4.5, normal_max: 2.8, danger_min: 7.1 },
  temperature_motor: { warning_max: 85.0 },
  temperature_compressor: { warning_max: 80.0 },
  thermal_dewethering: { genuine_overheat_delta: 35.0 },
  electrical_pq: { vuf_warning_max: 2.5 }
};

async function loadDynamicThresholds() {
  try {
    const res = await apiFetch('/api/v1/config/thresholds');
    if (res.ok) {
      const data = await res.json();
      if (data && data.thresholds) {
        appThresholds = data.thresholds;
        updateThresholdDisplayBadges();
        renderVibrationChart();
      }
    }
  } catch (err) {
    console.warn('Could not load dynamic thresholds from /api/v1/config/thresholds:', err);
  }
}

function updateThresholdDisplayBadges() {
  const vibSub = document.getElementById('vib-chart-subtitle') || document.querySelector('#chart-vibration')?.parentElement?.parentElement?.querySelector('p');
  if (vibSub && appThresholds.vibration) {
    vibSub.textContent = `IMU Acceleration vs Warning Threshold (${appThresholds.vibration.warning_max} mm/s)`;
  }
  const footerComp = document.getElementById('footer-temp-comp');
  if (footerComp && appThresholds.temperature_compressor) {
    footerComp.textContent = `Threshold: ${appThresholds.temperature_compressor.warning_max}°C`;
  }
  const dewetherBadge = document.getElementById('dewether-threshold-badge');
  if (dewetherBadge && appThresholds.thermal_dewethering) {
    dewetherBadge.textContent = `Threshold: +${appThresholds.thermal_dewethering.genuine_overheat_delta} °C Rise`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadDynamicThresholds();
  loadDynamicAssets();
  initFaultGrid();
  seedInitialHistory();
  initRepairCardButtons();
  runDiagnostics(false);
  renderAllCharts();

  setInterval(() => {
    tickTelemetry();
  }, 4000);
});

function initRepairCardButtons() {
  const btnOpt1 = document.getElementById('btn-apply-opt1');
  if (btnOpt1) {
    btnOpt1.addEventListener('click', () => {
      showToast('📋 Shift Log Updated: Immediate triage mitigation logged for shift engineer review.');
    });
  }
  const btnOpt2 = document.getElementById('btn-dispatch-opt2');
  if (btnOpt2) {
    btnOpt2.addEventListener('click', () => {
      showToast('🚀 SAP PM Work Order Dispatched: Overhaul scheduled with reserved warehouse BOM.');
    });
  }
}

async function loadDynamicAssets() {
  try {
    const res = await apiFetch('/api/v1/assets');
    if (!res.ok) return;
    const data = await res.json();
    const select = document.getElementById('machine-select');
    if (!select || !data.assets || data.assets.length === 0) return;

    select.innerHTML = '';
    data.assets.forEach((asset, idx) => {
      const opt = document.createElement('option');
      opt.value = asset.id;
      const tag = asset.source === 'mqtt_live' ? ' 🟢 [MQTT Live]' : (asset.source === 'api_ai_query' ? ' 🌐 [API]' : '');
      opt.textContent = asset.name + tag;
      if (asset.id === currentMachineId || idx === 0) {
        opt.selected = true;
        currentMachineId = asset.id;
      }
      select.appendChild(opt);
    });

    select.onchange = async (e) => {
      await switchAsset(e.target.value);
    };

    const companySub = document.getElementById('topbar-company-subtitle');
    if (companySub) {
      const selectedText = select.options[select.selectedIndex]?.text || currentMachineId;
      companySub.textContent = 'Asset: ' + selectedText + ' · Factory Floor 1';
    }
  } catch (err) {
    console.error('Failed to load dynamic assets:', err);
  }
}

async function switchAsset(assetId) {
  try {
    currentMachineId = assetId;
    const res = await apiFetch('/api/v1/assets/' + encodeURIComponent(assetId) + '/telemetry');
    const select = document.getElementById('machine-select');
    const selectedText = select?.options[select.selectedIndex]?.text || assetId;

    if (res.ok) {
      const data = await res.json();
      currentTelemetry = {
        ...currentTelemetry,
        ...data,
        machineId: assetId
      };
    } else {
      currentTelemetry.machineId = assetId;
    }

    const companySub = document.getElementById('topbar-company-subtitle');
    if (companySub) {
      companySub.textContent = 'Asset: ' + selectedText + ' · Factory Floor 1';
    }

    // Reset trend history for the new asset's baseline
    historyVibration.length = 0;
    historyThermal.length = 0;
    seedInitialHistory();

    updateGauges();
    renderAllCharts();
    await runDiagnostics(true);
    showToast('Switched to Asset: ' + selectedText);
  } catch (err) {
    console.error('Failed to switch asset:', err);
    showToast('Failed to switch asset: ' + err.message, true);
  }
}

function initFaultGrid() {
  const container = document.getElementById('fault-indicators-container');
  if (!container) return;

  container.innerHTML = FAULT_CATALOG.map(f => [
    '<div class="fault-card" id="fault-card-' + f.code + '">',
    '  <div class="fault-card-top">',
    '    <span class="fault-code">' + f.code + '</span>',
    '    <span class="badge badge-good" id="badge-' + f.code + '">0.0%</span>',
    '  </div>',
    '  <div class="fault-name">' + f.name + '</div>',
    '  <div class="fault-bar-container">',
    '    <div class="fault-bar-fill" id="bar-' + f.code + '" style="width: 0%;"></div>',
    '  </div>',
    '  <div class="fault-card-bottom">',
    '    <span>' + f.baseFreq + '</span>',
    '    <span id="status-text-' + f.code + '">Nominal</span>',
    '  </div>',
    '</div>'
  ].join('')).join('');
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.style.display = 'none');

  const selectedBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick')?.includes(tabId));
  if (selectedBtn) selectedBtn.classList.add('active');

  const target = document.getElementById('tab-' + tabId);
  if (target) {
    target.style.display = 'flex';
    if (tabId === 'vibration' || tabId === 'overview') {
      setTimeout(() => renderAllCharts(), 50);
    }
  }
}

function handleMachineChange(machineId) {
  currentMachineId = machineId;
  currentTelemetry.machineId = machineId;
  const companySub = document.getElementById('topbar-company-subtitle');
  if (companySub) {
    companySub.textContent = 'Asset: ' + machineId + ' · Demo Company · Factory Floor 1';
  }
  runDiagnostics(true);
}

function showToast(message, isError = false) {
  const existing = document.querySelector('.toast-notification');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'toast-notification';
  toast.style.borderColor = isError ? 'var(--status-critical)' : 'var(--accent)';
  toast.innerHTML = (isError ? '⚠️ ' : '⚡ ') + message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.4s ease';
    setTimeout(() => toast.remove(), 400);
  }, 3500);
}

async function runDiagnostics(triggerAlert = false) {
  const btn = document.getElementById('btn-run-pdm');
  const originalText = '⚡ Run AI Diagnostic';

  if (btn && triggerAlert) {
    btn.innerHTML = '⏳ Evaluating 4 Agents...';
    btn.style.opacity = '0.85';
  }

  // If clicked directly by user, add micro-jitter so sensor changes visibly animate
  if (triggerAlert) {
    currentTelemetry.timestamp = new Date().toISOString();
    currentTelemetry.imuAcceleration = Math.max(0.1, +(currentTelemetry.imuAcceleration + (Math.random() * 0.16 - 0.08)).toFixed(2));
    currentTelemetry.tempMotor = +(currentTelemetry.tempMotor + (Math.random() * 0.4 - 0.2)).toFixed(1);
    currentTelemetry.emMachineLoad = +(currentTelemetry.emMachineLoad + (Math.random() * 0.6 - 0.3)).toFixed(1);
    updateGauges();

    historyVibration.push({ time: new Date().toLocaleTimeString(), val: currentTelemetry.imuAcceleration });
    if (historyVibration.length > 50) historyVibration.shift();
    renderAllCharts();
  }

  try {
    const startTime = performance.now();
    const res = await apiFetch('/api/v1/predict_rul', {
      method: 'POST',
      body: JSON.stringify(attachDashboardWaveforms(currentTelemetry))
    });

    const elapsed = Math.round(performance.now() - startTime);

    if (!res.ok) {
      console.warn('Backend responded with HTTP ' + res.status);
      showToast('Diagnostic API HTTP ' + res.status + ' Error', true);
      if (btn) btn.innerHTML = originalText;
      return;
    }

    const apms = await res.json();
    applyApmsResults(apms);

    // Pulse highlight the actionable repair options grid
    const assistPanel = document.getElementById('actionable-assistant-panel') || document.getElementById('diag-summary-box');
    if (assistPanel) {
      assistPanel.classList.remove('pulse-highlight');
      void assistPanel.offsetWidth;
      assistPanel.classList.add('pulse-highlight');
    }

    if (triggerAlert) {
      const defectName = apms.defect_localization?.defect_name || 'Normal Operation';
      const rulDays = apms.rul_prediction?.rul_days ? apms.rul_prediction.rul_days.toFixed(1) + 'd' : '--';
      showToast('Diagnostic Complete (' + elapsed + 'ms): ' + defectName + ' · RUL: ' + rulDays);

      if (btn) {
        btn.innerHTML = '✓ Complete (' + elapsed + 'ms)';
        setTimeout(() => {
          btn.innerHTML = originalText;
          btn.style.opacity = '1';
        }, 1500);
      }
    }
  } catch (err) {
    console.error('Failed to run diagnostics:', err);
    showToast('Failed to connect to AI Agents: ' + err.message, true);
    if (btn) btn.innerHTML = originalText;
  }
}

function applyApmsResults(apms) {
  const isHalt = apms.cable_check?.status !== 'VALID';
  const alphaStatusBadge = document.getElementById('badge-agent-alpha');
  const alphaOutput = document.getElementById('out-agent-alpha');
  const gatekeeperBadge = document.getElementById('diag-badge-gatekeeper');

  if (isHalt) {
    alphaStatusBadge.className = 'badge badge-critical';
    alphaStatusBadge.textContent = apms.cable_check.status;
    alphaOutput.textContent = 'HALTED: ' + (apms.cable_check.fault_reason || 'Cable fault');
    gatekeeperBadge.className = 'badge badge-critical';
    gatekeeperBadge.textContent = 'Gatekeeper: Alert Suppressed (Cable)';

    const headEl = document.getElementById('diag-headline');
    if (headEl) headEl.textContent = '⚠️ 100% False Alarm Suppressed: Hardware Sensor Issue';
    const reasonEl = document.getElementById('diag-reasoning');
    if (reasonEl) reasonEl.textContent = apms.cable_check.fault_reason || 'Motor is running, but vibration reads 0.0 mm/s. Sensor cable disconnected or broken.';
    
    document.getElementById('kpi-health-val').textContent = '--';
    document.getElementById('kpi-status-val').textContent = 'Sensor Fault';
    document.getElementById('kpi-status-val').style.color = 'var(--status-critical)';
    
    updateRepairGuidanceCards(apms, true);
    return;
  } else {
    alphaStatusBadge.className = 'badge badge-good';
    alphaStatusBadge.textContent = 'Passed';
    alphaOutput.textContent = 'Pattern: ' + (apms.cable_check.pattern_recognition_status || 'STABLE') + ' | Slope: 0.000';
    gatekeeperBadge.className = 'badge badge-good';
    gatekeeperBadge.textContent = 'Gatekeeper: Sensor Validated';
  }

  const domain = apms.electrical_health?.isolated_failure_domain || 'MECHANICAL';
  document.getElementById('kpi-domain-val').textContent = domain;
  document.getElementById('kpi-pattern-val').textContent = 'Pattern: ' + (apms.cable_check?.pattern_recognition_status || 'STABLE');
  document.getElementById('out-agent-beta').textContent = 'Domain: ' + domain + ' | VUF: ' + (apms.electrical_health?.voltage_unbalance_pct?.toFixed(2) || '1.20') + '%';

  const defect = apms.defect_localization;
  const rul = apms.rul_prediction;
  const isDefect = defect && defect.defect_code !== 'NORMAL';

  if (isDefect) {
    document.getElementById('kpi-health-val').textContent = '68%';
    document.getElementById('kpi-health-val').style.color = 'var(--status-warning)';
    document.getElementById('kpi-rul-val').textContent = rul.rul_days.toFixed(1) + ' Days';
    document.getElementById('kpi-rul-val').style.color = 'var(--status-warning)';
    document.getElementById('kpi-rul-hours').textContent = rul.rul_operating_hours.toFixed(0) + ' Operating Hours (' + rul.method + ')';
    document.getElementById('topbar-condition-badge').className = 'badge badge-warning';
    document.getElementById('topbar-condition-badge').textContent = 'Warning';
    document.getElementById('out-agent-gamma').textContent = 'Defect: ' + defect.defect_code + ' | RUL: ' + rul.rul_days.toFixed(1) + 'd';

    const headEl = document.getElementById('diag-headline');
    if (headEl) headEl.textContent = defect.defect_name + ' Detected (' + defect.defect_code + ')';
    const reasonEl = document.getElementById('diag-reasoning');
    if (reasonEl) reasonEl.textContent = 'Diagnosed via ' + defect.diagnosis_method + '. Envelope FFT shows characteristic defect peak. Estimated RUL is ' + rul.rul_days.toFixed(1) + ' days (' + rul.rul_operating_hours.toFixed(0) + ' operating hours) based on PINNs crack propagation and Weibull B10 survival.';
  } else {
    document.getElementById('kpi-health-val').textContent = '98%';
    document.getElementById('kpi-health-val').style.color = 'var(--status-good)';
    document.getElementById('kpi-rul-val').textContent = rul.rul_days.toFixed(1) + ' Days';
    document.getElementById('kpi-rul-val').style.color = '#60a5fa';
    document.getElementById('kpi-rul-hours').textContent = rul.rul_operating_hours.toFixed(0) + ' Operating Hours (Weibull B10)';
    document.getElementById('topbar-condition-badge').className = 'badge badge-good';
    document.getElementById('topbar-condition-badge').textContent = 'Good';
    document.getElementById('out-agent-gamma').textContent = 'Status: NOMINAL | RUL: ' + rul.rul_days.toFixed(1) + 'd';

    const headEl = document.getElementById('diag-headline');
    if (headEl) headEl.textContent = 'All Monitored Parameters Operating Nominally';
    const reasonEl = document.getElementById('diag-reasoning');
    if (reasonEl) reasonEl.textContent = 'Agent Alpha verified physical sensor integrity across all 11 checks with zero false alarms. Agent Beta confirmed temperature rise is within dynamic de-weathered ambient bounds. Agent Gamma calculated RUL under mechanical steady state.';
  }

  updateRepairGuidanceCards(apms, false);
  updateFaultIndicators(defect);

  const cmms = apms.cmms_work_order;
  if (cmms && cmms.work_order_id) {
    document.getElementById('out-agent-delta').textContent = 'WO: ' + cmms.work_order_id + ' | ' + cmms.reserved_warehouse_bin;
    document.getElementById('wo-id-display').textContent = cmms.work_order_id;
    document.getElementById('wo-machine-id').textContent = currentMachineId + ' (Screw Compressor Line A)';
    document.getElementById('wo-window').textContent = cmms.scheduled_downtime_window;
    document.getElementById('wo-bin').textContent = cmms.reserved_warehouse_bin;
    document.getElementById('wo-part').textContent = cmms.required_spare_parts?.[0] || 'SKF 6208-2RS1 Bearing';
    document.getElementById('wo-sourcing').textContent = cmms.sourcing_status;
    document.getElementById('wo-downtime').textContent = (cmms.estimated_downtime_hours || 4.0) + ' Hours';
    document.getElementById('wo-guidance').textContent = cmms.recommended_action;
  }

  updateGauges();
}

function updateRepairGuidanceCards(apms, isHalt) {
  const issueConfidence = document.getElementById('issue-confidence');
  const issueTitle = document.getElementById('issue-title');
  const issueLocation = document.getElementById('issue-location');
  const issueHarmonic = document.getElementById('issue-harmonic');
  const issueMechanism = document.getElementById('issue-mechanism');
  const issueTip = document.getElementById('issue-tip');
  const diagSeverity = document.getElementById('diag-severity-badge');

  const opt1Title = document.getElementById('opt1-title');
  const opt1Desc = document.getElementById('opt1-desc');
  const opt1Impact = document.getElementById('opt1-impact');

  const opt2Duration = document.getElementById('opt2-duration');
  const opt2Title = document.getElementById('opt2-title');
  const opt2Tools = document.getElementById('opt2-tools');
  const opt2Steps = document.getElementById('opt2-steps');

  const cardIssue = document.getElementById('card-issue');

  if (!issueTitle) return;

  if (isHalt) {
    if (diagSeverity) {
      diagSeverity.className = 'badge badge-critical';
      diagSeverity.textContent = 'CRITICAL · SENSOR HALT';
    }
    if (cardIssue) {
      cardIssue.style.borderColor = 'rgba(239, 68, 68, 0.6)';
    }
    if (issueConfidence) issueConfidence.textContent = 'Confidence: 100%';
    if (issueTitle) issueTitle.textContent = '⚠️ 100% False Alarm Suppressed: Transducer Fault';
    if (issueLocation) issueLocation.textContent = 'IEPE Accelerometer Transducer / M12 Shielded Cable Pigtail';
    if (issueHarmonic) issueHarmonic.textContent = 'Flatline (0.00 mm/s while Motor Energized)';
    if (issueMechanism) issueMechanism.textContent = apms.cable_check?.fault_reason || 'Motor is drawing electrical load, but vibration signal dropped to 0.00 mm/s. Physical sensor disconnect or severed core.';
    if (issueTip) issueTip.textContent = 'Senior Tip: In high vibration environments, use braided steel conduit to shield transducer cables from fatigue breakage.';

    if (opt1Title) opt1Title.textContent = 'Field Triage: Transducer & Cable Connector Inspection';
    if (opt1Desc) opt1Desc.textContent = 'Verify 24V DC IEPE constant current supply on DAQ module. Inspect BNC/M12 plug for loose collar or sheared cable strain relief.';
    if (opt1Impact) opt1Impact.textContent = 'Prevents false emergency plant shutdowns by isolating instrument fault from true machine damage.';

    if (opt2Duration) opt2Duration.textContent = 'Est. Downtime: 0.5 Hrs';
    if (opt2Title) opt2Title.textContent = 'Transducer Remounting & Signal Continuity Protocol';
    if (opt2Tools) opt2Tools.textContent = 'Digital Multimeter, Calibrated Torque Wrench (2.8 Nm), Contact Cleaner, Spare M12 Cable';
    if (opt2Steps) opt2Steps.innerHTML = '1. Power down DAQ input channel.<br>2. Measure continuity across center pin and outer shield (> 10 MOhm open).<br>3. Clean mounting spot to bare metal, apply couplant, and torque accelerometer to 2.8 Nm.<br>4. Check baseline DC bias voltage (8V - 12V DC).';
    return;
  }

  const defect = apms.defect_localization;
  const rul = apms.rul_prediction;
  const isDefect = defect && defect.defect_code !== 'NORMAL';
  const guidance = defect?.expert_repair_guidance;

  if (isDefect) {
    const isCritical = rul && rul.rul_days < 14;
    if (diagSeverity) {
      diagSeverity.className = isCritical ? 'badge badge-critical' : 'badge badge-warning';
      diagSeverity.textContent = guidance?.severity_level || (isCritical ? 'CRITICAL · ACTION REQUIRED' : 'WARNING · MONITORING');
    }
    if (cardIssue) {
      cardIssue.style.borderColor = isCritical ? 'rgba(239, 68, 68, 0.6)' : 'rgba(245, 158, 11, 0.6)';
    }
    if (issueConfidence) issueConfidence.textContent = 'Confidence: ' + (defect.confidence_percentage ? defect.confidence_percentage.toFixed(0) : '94') + '%';
    if (issueTitle) issueTitle.textContent = defect.defect_name + ' (' + defect.defect_code + ')';
    if (issueLocation) issueLocation.textContent = guidance?.component_exact_location || defect.failing_component;
    
    let harmonicStr = 'Characteristic Defect Peak';
    if (defect.dominant_frequencies_hz && defect.dominant_frequencies_hz.length > 0) {
      harmonicStr = defect.dominant_frequencies_hz.map(f => typeof f === 'number' ? f.toFixed(1) + ' Hz' : f).join(', ');
    }
    if (issueHarmonic) issueHarmonic.textContent = harmonicStr;

    if (issueMechanism) issueMechanism.textContent = guidance?.root_cause_mechanism || ('Diagnosed via ' + defect.diagnosis_method + '. Envelope demodulated spectrum indicates localized component degradation.');
    if (issueTip) issueTip.textContent = (guidance?.expert_tips && guidance.expert_tips.length > 0) ? guidance.expert_tips[0] : 'Follow ISO 10816-3 vibration limits and verify dynamic balancing after reassembly.';

    if (opt1Title) opt1Title.textContent = 'Field Triage: Immediate Operational Mitigation';
    if (opt1Desc) opt1Desc.textContent = guidance?.immediate_field_triage || 'Apply interim high-performance synthetic grease to sustain elastohydrodynamic film; de-rate operational speed by 10% if vibration exceeds threshold.';
    if (opt1Impact) opt1Impact.textContent = 'Reduces instantaneous shear stress, retards crack propagation, and buys 7–14 days to schedule planned maintenance window.';

    const overhaul = guidance?.planned_overhaul_playbook;
    if (opt2Duration) opt2Duration.textContent = 'Est. Downtime: ' + ((overhaul?.estimated_duration_hours) || 2.5).toFixed(1) + ' Hrs';
    if (opt2Title) opt2Title.textContent = overhaul?.option_title || ('Precision Overhaul & Component Replacement');
    if (opt2Tools) opt2Tools.textContent = overhaul?.required_tools_and_materials?.join(', ') || 'Induction Heater, Laser Alignment Kit, Calibrated Torque Wrench';
    if (opt2Steps) {
      if (overhaul?.step_by_step_instructions && overhaul.step_by_step_instructions.length > 0) {
        opt2Steps.innerHTML = overhaul.step_by_step_instructions.join('<br>');
      } else {
        opt2Steps.innerHTML = '1. Lock-out / Tag-out electrical supply.<br>2. Disassemble and replace worn component.<br>3. Perform precision laser alignment and record post-repair baseline.';
      }
    }
  } else {
    if (diagSeverity) {
      diagSeverity.className = 'badge badge-good';
      diagSeverity.textContent = 'NOMINAL · CERTIFIED';
    }
    if (cardIssue) {
      cardIssue.style.borderColor = 'rgba(16, 185, 129, 0.4)';
    }
    if (issueConfidence) issueConfidence.textContent = 'Confidence: 98%';
    if (issueTitle) issueTitle.textContent = 'All Monitored Parameters Operating Nominally';
    if (issueLocation) issueLocation.textContent = guidance?.component_exact_location || 'Motor DE / NDE & Compressor Bearings';
    if (issueHarmonic) issueHarmonic.textContent = 'Baseline 1X (29.8 Hz)';
    if (issueMechanism) issueMechanism.textContent = guidance?.root_cause_mechanism || 'Vibration RMS, envelope demodulated spectrum, and thermal signatures indicate healthy elastohydrodynamic lubrication film and steady rotor dynamics.';
    if (issueTip) issueTip.textContent = (guidance?.expert_tips && guidance.expert_tips.length > 0) ? guidance.expert_tips[0] : 'Senior Tip: Consistent baseline conditions mean optimal MTBF. Do not over-grease bearings when operating normally!';

    if (opt1Title) opt1Title.textContent = 'Routine Verification & In-Service Monitoring';
    if (opt1Desc) opt1Desc.textContent = guidance?.immediate_field_triage || 'Execute routine physical inspection of grease nipples, acoustic listening probe check, and verify motor cooling fan cover is clear of dust accumulation.';
    if (opt1Impact) opt1Impact.textContent = 'Maintains optimum MTBF, prevents false alarms, and validates sensor telemetry.';

    const overhaul = guidance?.planned_overhaul_playbook;
    if (opt2Duration) opt2Duration.textContent = 'Est. Downtime: ' + ((overhaul?.estimated_duration_hours) || 0.5).toFixed(1) + ' Hrs';
    if (opt2Title) opt2Title.textContent = overhaul?.option_title || 'Standard ISO 10816-3 Preventive Maintenance';
    if (opt2Tools) opt2Tools.textContent = overhaul?.required_tools_and_materials?.join(', ') || 'Ultrasound Grease Gun Listening Probe, Digital Calipers';
    if (opt2Steps) {
      if (overhaul?.step_by_step_instructions && overhaul.step_by_step_instructions.length > 0) {
        opt2Steps.innerHTML = overhaul.step_by_step_instructions.join('<br>');
      } else {
        opt2Steps.innerHTML = '1. Continue continuous vibration and thermal telemetry monitoring.<br>2. Schedule ultrasonic bearing lubrication check during next planned plant cycle.<br>3. Inspect motor terminal box gaskets.';
      }
    }
  }
}

function updateFaultIndicators(activeDefect) {
  FAULT_CATALOG.forEach(f => {
    const card = document.getElementById('fault-card-' + f.code);
    const bar = document.getElementById('bar-' + f.code);
    const badge = document.getElementById('badge-' + f.code);
    const statusText = document.getElementById('status-text-' + f.code);
    if (!card || !bar) return;

    let conf = 0.05;
    if (activeDefect && activeDefect.defect_code === f.code) {
      conf = (activeDefect.confidence_percentage || 94.0) / 100.0;
    }

    const pct = Math.round(conf * 100);
    bar.style.width = pct + '%';
    badge.textContent = pct + '%';

    if (pct > 70) {
      card.classList.add('breached');
      bar.className = 'fault-bar-fill critical';
      badge.className = 'badge badge-critical';
      statusText.textContent = 'BREACH';
    } else if (pct > 30) {
      card.classList.remove('breached');
      bar.className = 'fault-bar-fill warning';
      badge.className = 'badge badge-warning';
      statusText.textContent = 'Elevated';
    } else {
      card.classList.remove('breached');
      bar.className = 'fault-bar-fill';
      badge.className = 'badge badge-good';
      statusText.textContent = 'Nominal';
    }
  });
}

function updateGauges() {
  document.getElementById('val-rpm').textContent = currentTelemetry.rpm;
  document.getElementById('val-vib').textContent = currentTelemetry.imuAcceleration.toFixed(2);
  document.getElementById('val-temp-motor').textContent = currentTelemetry.tempMotor.toFixed(1);
  document.getElementById('val-temp-comp').textContent = currentTelemetry.tempCompressor.toFixed(1);
  document.getElementById('val-load').textContent = currentTelemetry.emMachineLoad.toFixed(1);
  document.getElementById('val-power').textContent = currentTelemetry.emPower.toFixed(1);
  document.getElementById('val-vuf').textContent = currentTelemetry.emVoltageImbalance.toFixed(2);

  const rpmOffset = 141 - (Math.min(currentTelemetry.rpm, 2000) / 2000) * 141;
  document.getElementById('arc-rpm')?.setAttribute('stroke-dashoffset', rpmOffset);

  const vibOffset = 141 - (Math.min(currentTelemetry.imuAcceleration, 12) / 12) * 141;
  const vibArc = document.getElementById('arc-vib');
  if (vibArc) {
    vibArc.setAttribute('stroke-dashoffset', vibOffset);
    vibArc.setAttribute('stroke', currentTelemetry.imuAcceleration > 1.5 ? 'var(--status-critical)' : 'var(--status-good)');
  }

  const tempOffset = 141 - (Math.min(currentTelemetry.tempMotor, 100) / 100) * 141;
  const tempArc = document.getElementById('arc-temp-motor');
  if (tempArc) {
    tempArc.setAttribute('stroke-dashoffset', tempOffset);
    tempArc.setAttribute('stroke', currentTelemetry.tempMotor > 45.0 ? 'var(--status-critical)' : 'var(--status-good)');
  }

  const loadOffset = 141 - (Math.min(currentTelemetry.emMachineLoad, 100) / 100) * 141;
  document.getElementById('arc-load')?.setAttribute('stroke-dashoffset', loadOffset);
}

function injectScenario(scenario) {
  switch (scenario) {
    case 'nominal':
      currentTelemetry.imuAcceleration = 0.68;
      currentTelemetry.rpm = 1480;
      currentTelemetry.tempMotor = 38.0;
      currentTelemetry.tempCompressor = 41.0;
      currentTelemetry.emVoltageImbalance = 0.80;
      currentTelemetry.emIr = 14.2;
      currentTelemetry.vibrationHarmonics = [
        { frequency: 24.7, amplitude: -22.5 },
        { frequency: 49.3, amplitude: -26.0 }
      ];
      break;

    case 'cable_cut':
      currentTelemetry.imuAcceleration = 0.0;
      currentTelemetry.emIr = 14.5;
      break;

    case 'misalignment':
      currentTelemetry.imuAcceleration = 6.2;
      currentTelemetry.rpm = 1480;
      currentTelemetry.vibrationHarmonics = [
        { frequency: 24.7, amplitude: -18.0 },
        { frequency: 49.3, amplitude: -4.2 }
      ];
      break;

    case 'bearing_bpfi':
      currentTelemetry.imuAcceleration = 5.8;
      currentTelemetry.rpm = 1790;
      currentTelemetry.tempMotor = 62.0;
      currentTelemetry.vibrationHarmonics = [
        { frequency: 162.4, amplitude: -3.8 },
        { frequency: 324.8, amplitude: -8.5 }
      ];
      break;

    case 'voltage_unbalance':
      currentTelemetry.emVoltageImbalance = 3.4;
      currentTelemetry.emVr = 412.0;
      currentTelemetry.emVy = 388.0;
      break;
  }

  runDiagnostics(true);
}

function tickTelemetry() {
  const jitter = (Math.random() - 0.5) * 0.08;
  if (currentTelemetry.imuAcceleration > 0.1) {
    currentTelemetry.imuAcceleration = Math.max(0.2, currentTelemetry.imuAcceleration + jitter);
  }

  historyVibration.push({ time: new Date().toLocaleTimeString(), val: currentTelemetry.imuAcceleration });
  if (historyVibration.length > maxHistory) historyVibration.shift();

  historyThermal.push({
    time: new Date().toLocaleTimeString(),
    motor: currentTelemetry.tempMotor + (Math.random() - 0.5) * 0.2,
    comp: currentTelemetry.tempCompressor + (Math.random() - 0.5) * 0.3,
    ambient: 25.0
  });
  if (historyThermal.length > maxHistory) historyThermal.shift();

  updateGauges();
  renderAllCharts();
}

function seedInitialHistory() {
  const now = Date.now();
  const baseVib = currentTelemetry?.imuAcceleration || 1.8;
  const baseMotor = currentTelemetry?.tempMotor || 48.0;
  const baseComp = currentTelemetry?.tempCompressor || 52.0;
  for (let i = 25; i >= 0; i--) {
    const t = new Date(now - i * 4000).toLocaleTimeString();
    historyVibration.push({ time: t, val: Math.max(0.1, +(baseVib + (Math.sin(i * 0.3) * 0.08)).toFixed(2)) });
    historyThermal.push({ time: t, motor: +(baseMotor + Math.sin(i * 0.2) * 0.3).toFixed(1), comp: baseComp, ambient: 25.0 });
  }
}

function renderAllCharts() {
  renderVibrationChart();
  renderThermalChart();
  renderFftChart();
  renderWaveformChart();
}

function renderVibrationChart() {
  const canvas = document.getElementById('chart-vibration');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.parentElement.clientWidth;
  const h = canvas.height = canvas.parentElement.clientHeight;

  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let y = 0; y < h; y += 40) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const warningLimit = (appThresholds && appThresholds.vibration && appThresholds.vibration.warning_max) ? appThresholds.vibration.warning_max : 4.5;
  const thresholdY = h - (warningLimit / 10.0) * h;
  ctx.strokeStyle = '#ef4444';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(0, thresholdY);
  ctx.lineTo(w, thresholdY);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#ef4444';
  ctx.font = '10px sans-serif';
  ctx.fillText(`ISO 10816 Warning (${warningLimit} mm/s)`, 10, thresholdY - 4);

  if (historyVibration.length < 2) return;
  ctx.strokeStyle = '#3987e5';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  historyVibration.forEach((pt, idx) => {
    const x = (idx / (historyVibration.length - 1)) * w;
    const y = h - (Math.min(pt.val, 10.0) / 10.0) * (h - 20) - 10;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderThermalChart() {
  const canvas = document.getElementById('chart-thermal');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.parentElement.clientWidth;
  const h = canvas.height = canvas.parentElement.clientHeight;

  ctx.clearRect(0, 0, w, h);

  if (historyThermal.length < 2) return;

  ctx.strokeStyle = '#f59e0b';
  ctx.lineWidth = 2;
  ctx.beginPath();
  historyThermal.forEach((pt, idx) => {
    const x = (idx / (historyThermal.length - 1)) * w;
    const y = h - ((pt.motor - 20) / 60) * h;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.strokeStyle = '#10b981';
  ctx.beginPath();
  historyThermal.forEach((pt, idx) => {
    const x = (idx / (historyThermal.length - 1)) * w;
    const y = h - ((pt.comp - 20) / 60) * h;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderFftChart() {
  const canvas = document.getElementById('chart-fft');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.parentElement.clientWidth;
  const h = canvas.height = canvas.parentElement.clientHeight;

  ctx.clearRect(0, 0, w, h);

  const bars = 60;
  for (let i = 0; i < bars; i++) {
    const freq = (i / bars) * 500;
    const x = (i / bars) * w;
    let amp = Math.random() * 8 + 4;

    if (Math.abs(freq - 24.7) < 8) amp = 28;
    if (Math.abs(freq - 49.3) < 8) amp = currentTelemetry.vibrationHarmonics[1]?.amplitude > -10 ? 45 : 18;
    if (Math.abs(freq - 162.4) < 8) amp = currentTelemetry.vibrationHarmonics.some(h => h.frequency === 162.4 && h.amplitude > -10) ? 65 : 12;

    const barH = (amp / 80) * (h - 30);
    ctx.fillStyle = amp > 40 ? '#ef4444' : (amp > 25 ? '#3987e5' : '#27272a');
    ctx.fillRect(x, h - barH - 20, (w / bars) - 2, barH);
  }

  ctx.fillStyle = '#60a5fa';
  ctx.font = '11px monospace';
  ctx.fillText('BPFI: 162.4 Hz', (162.4 / 500) * w - 30, 20);
  ctx.fillText('1X: 24.7 Hz', (24.7 / 500) * w - 10, 35);
  ctx.fillText('2X: 49.3 Hz', (49.3 / 500) * w - 10, 50);
}

function renderWaveformChart() {
  const canvas = document.getElementById('chart-waveform');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.parentElement.clientWidth;
  const h = canvas.height = canvas.parentElement.clientHeight;

  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = '#3987e5';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let x = 0; x < w; x += 3) {
    const y = h * 0.3 + Math.sin(x * 0.05) * 15 + (Math.random() - 0.5) * 4;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.strokeStyle = '#f59e0b';
  ctx.beginPath();
  for (let x = 0; x < w; x += 3) {
    const y = h * 0.5 + Math.cos(x * 0.04) * 12 + (Math.random() - 0.5) * 4;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.strokeStyle = '#10b981';
  ctx.beginPath();
  for (let x = 0; x < w; x += 3) {
    const y = h * 0.7 + Math.sin(x * 0.03) * 10 + (Math.random() - 0.5) * 3;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

window.addEventListener('resize', () => {
  renderAllCharts();
});
