// API Service & Sync Dispatcher
const API = {
  baseUrl: '',

  getToken: function() {
    return localStorage.getItem('asha_auth_token') || '';
  },

  setToken: function(token) {
    localStorage.setItem('asha_auth_token', token);
  },

  getHeaders: function() {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  },

  request: async function(endpoint, options = {}) {
    // If user has toggled simulated offline mode, reject immediately so offline fallback engages!
    if (window.AppState && window.AppState.isOfflineSimulated && !options.forceOnline) {
      throw new Error("NETWORK_OFFLINE_SIMULATED");
    }

    options.headers = Object.assign({}, this.getHeaders(), options.headers);
    try {
      const res = await fetch(endpoint, options);
      if (res.status === 401) {
        // Prompt re-login
        console.warn("Session expired or unauthorized");
      }
      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(errorText || `HTTP Error ${res.status}`);
      }
      return await res.json();
    } catch(err) {
      throw err;
    }
  },

  // Auth
  login: async function(username, password) {
    const data = await this.request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      forceOnline: true
    });
    this.setToken(data.access_token);
    return data;
  },

  // Beneficiaries
  getBeneficiaries: async function(params = {}) {
    const qs = new URLSearchParams(params).toString();
    try {
      const data = await this.request(`/api/beneficiaries?${qs}`);
      localStorage.setItem('asha_cache_beneficiary', JSON.stringify(data));
      return data;
    } catch(e) {
      console.log("Serving beneficiaries from local offline cache");
      return LocalDB.getLocalEntities('beneficiary');
    }
  },

  getBeneficiaryDetails: async function(id) {
    return await this.request(`/api/beneficiaries/${id}`);
  },

  registerBeneficiary: async function(benData) {
    if (window.AppState && window.AppState.isOfflineSimulated) {
      // Offline mode: generate internal temp ID & save to LocalDB sync queue
      benData.beneficiary_id = benData.beneficiary_id || `BEN-${new Date().getFullYear()}-OFF-${Math.random().toString(36).substring(2,6).toUpperCase()}`;
      LocalDB.enqueue('beneficiary', 'CREATE', benData);
      return {
        message: "Beneficiary registered offline. Enqueued for server synchronization.",
        beneficiary: benData,
        pregnancy: { beneficiary_id: benData.beneficiary_id, edd_date: benData.edd_date, status: 'ACTIVE' },
        is_offline_saved: true
      };
    }

    try {
      return await this.request('/api/beneficiaries', {
        method: 'POST',
        body: JSON.stringify(benData)
      });
    } catch(e) {
      // Fallback to offline enqueue if unexpected network failure
      LocalDB.enqueue('beneficiary', 'CREATE', benData);
      return {
        message: "Network unavailable. Saved locally to sync queue.",
        beneficiary: benData,
        is_offline_saved: true
      };
    }
  },

  // ANC
  getANCDueList: async function(status = '') {
    try {
      const data = await this.request(`/api/anc/due-list?status_filter=${status}`);
      localStorage.setItem('asha_cache_anc_visit', JSON.stringify(data));
      return data;
    } catch(e) {
      return LocalDB.getLocalEntities('anc_visit');
    }
  },

  recordANCVisit: async function(visitData) {
    if (window.AppState && window.AppState.isOfflineSimulated) {
      visitData.visit_uuid = visitData.visit_uuid || `ANC-OFF-${Date.now()}`;
      LocalDB.enqueue('anc_visit', 'CREATE', visitData);
      return { message: "ANC visit recorded offline. Queued for sync.", visit: visitData, is_offline_saved: true };
    }
    try {
      return await this.request('/api/anc/record-visit', {
        method: 'POST',
        body: JSON.stringify(visitData)
      });
    } catch(e) {
      LocalDB.enqueue('anc_visit', 'CREATE', visitData);
      return { message: "Network error. Recorded in local offline queue.", visit: visitData, is_offline_saved: true };
    }
  },

  // Home Visits
  recordHomeVisit: async function(visitData) {
    if (window.AppState && window.AppState.isOfflineSimulated) {
      visitData.visit_uuid = visitData.visit_uuid || `HV-OFF-${Date.now()}`;
      LocalDB.enqueue('home_visit', 'CREATE', visitData);
      return { message: "Home visit saved locally.", home_visit: visitData, is_offline_saved: true };
    }
    try {
      return await this.request('/api/home-visits', {
        method: 'POST',
        body: JSON.stringify(visitData)
      });
    } catch(e) {
      LocalDB.enqueue('home_visit', 'CREATE', visitData);
      return { message: "Network unavailable. Saved to local queue.", home_visit: visitData, is_offline_saved: true };
    }
  },

  // High-Risk & Referrals
  getHighRiskCases: async function() {
    return await this.request('/api/high-risk');
  },

  getDangerSigns: async function() {
    return await this.request('/api/referrals/danger-signs');
  },

  getFacilities: async function() {
    return await this.request('/api/referrals/facilities');
  },

  initiateReferral: async function(refData) {
    if (window.AppState && window.AppState.isOfflineSimulated) {
      refData.referral_uuid = refData.referral_uuid || `REF-OFF-${Date.now()}`;
      LocalDB.enqueue('referral', 'CREATE', refData);
      return {
        message: "Referral logged in local emergency register.",
        emergency_instruction: "Immediately arrange 108 Emergency Ambulance. Place mother on left lateral side.",
        referral: refData,
        is_offline_saved: true
      };
    }
    return await this.request('/api/referrals/initiate', {
      method: 'POST',
      body: JSON.stringify(refData)
    });
  },

  // Postnatal Care
  getPNCDueList: async function() {
    return await this.request('/api/pnc/due-list');
  },

  recordPNCVisit: async function(pncData) {
    if (window.AppState && window.AppState.isOfflineSimulated) {
      pncData.pnc_uuid = pncData.pnc_uuid || `PNC-OFF-${Date.now()}`;
      LocalDB.enqueue('pnc_visit', 'CREATE', pncData);
      return { message: "PNC visit saved offline.", visit: pncData, is_offline_saved: true };
    }
    return await this.request('/api/pnc/record-visit', {
      method: 'POST',
      body: JSON.stringify(pncData)
    });
  },

  // Supervisor KPIs
  getSupervisorKPIs: async function(village = '') {
    return await this.request(`/api/supervisor/dashboard-kpis?village=${village}`);
  },

  getAuditLogs: async function() {
    return await this.request('/api/supervisor/audit-logs');
  },

  // RAG Assistant
  askAssistant: async function(query) {
    return await this.request('/api/assistant/query', {
      method: 'POST',
      body: JSON.stringify({ query })
    });
  },

  // Sync Push Engine
  syncNow: async function() {
    const queue = LocalDB.getQueue();
    if (queue.length === 0) {
      return { message: "Sync queue is empty. All records are up to date." };
    }

    const payload = { items: queue };
    const res = await this.request('/api/sync/push', {
      method: 'POST',
      body: JSON.stringify(payload),
      forceOnline: true
    });

    if (res.acknowledged_ids && res.acknowledged_ids.length > 0) {
      // Remove acknowledged items from queue
      let currentQ = LocalDB.getQueue();
      currentQ = currentQ.filter(i => !res.acknowledged_ids.includes(i.client_tx_id));
      LocalDB.saveQueue(currentQ);
    }

    return res;
  },

  // Government Portal Integration Test (JANANI / ANMOL)
  testGovSync: async function(benId) {
    return await this.request(`/api/sync/government/test?beneficiary_id=${benId}`, {
      method: 'POST',
      forceOnline: true
    });
  }
};

