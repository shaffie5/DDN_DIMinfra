/* ═══════════════════════════════════════════════════════════
   DDN Flask App — JavaScript
   ═══════════════════════════════════════════════════════════ */

// ─── Toast Notifications ──────────────────────────────────
function showToast(message, type) {
    type = type || 'info';
    const bgClass = {
        success: 'bg-success text-white',
        error: 'bg-danger text-white',
        warning: 'bg-warning text-dark',
        info: 'bg-info text-dark',
    }[type] || 'bg-secondary text-white';

    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toastEl = document.createElement('div');
    toastEl.className = 'toast align-items-center ' + bgClass + ' border-0';
    toastEl.setAttribute('role', 'alert');
    toastEl.innerHTML =
        '<div class="d-flex">' +
        '<div class="toast-body">' + message + '</div>' +
        '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
        '</div>';

    container.appendChild(toastEl);
    var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', function() { toastEl.remove(); });
}

// ─── Map (Create Note Page) ──────────────────────────────
var map, originMarker, destMarker, routeLine;

function initCreateNoteMap() {
    var mapEl = document.getElementById('routeMap');
    if (!mapEl) return;

    map = L.map('routeMap').setView([50.85, 4.35], 8);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap & CartoDB',
        maxZoom: 19,
    }).addTo(map);

    // Click handler
    map.on('click', function(e) {
        var mode = document.querySelector('input[name="pinMode"]:checked');
        var type = mode ? mode.value : 'origin';
        applyLocation(type, e.latlng.lat, e.latlng.lng, '');
        reverseGeocode(e.latlng.lat, e.latlng.lng, type);
    });
}

function applyLocation(type, lat, lon, label) {
    if (type === 'origin') {
        document.getElementById('plant_lat').value = lat;
        document.getElementById('plant_lon').value = lon;
        if (label) document.getElementById('plant_address').value = label;

        if (originMarker) map.removeLayer(originMarker);
        originMarker = L.marker([lat, lon], {
            icon: L.divIcon({
                html: '<i class="fas fa-industry" style="color:#2563eb;font-size:24px;"></i>',
                iconSize: [24, 24], className: ''
            }),
        }).addTo(map).bindTooltip('📍 Herkomst');

        updateOriginCard(label, lat, lon);
    } else {
        document.getElementById('site_lat').value = lat;
        document.getElementById('site_lon').value = lon;
        if (label) document.getElementById('site_address').value = label;

        if (destMarker) map.removeLayer(destMarker);
        destMarker = L.marker([lat, lon], {
            icon: L.divIcon({
                html: '<i class="fas fa-flag-checkered" style="color:#dc2626;font-size:24px;"></i>',
                iconSize: [24, 24], className: ''
            }),
        }).addTo(map).bindTooltip('🏁 Bestemming');

        updateDestCard(label, lat, lon);
        // Update readonly display
        var display = document.getElementById('siteAddressDisplay');
        if (display) display.value = label;
    }

    // Fit map and calculate route
    if (originMarker && destMarker) {
        var group = new L.featureGroup([originMarker, destMarker]);
        map.fitBounds(group.getBounds().pad(0.2));
        fetchRoute();
    }

    showToast(type === 'origin' ? '📍 Herkomst bijgewerkt' : '🏁 Bestemming bijgewerkt', 'success');
}

function updateOriginCard(label, lat, lon) {
    var card = document.getElementById('originCard');
    if (card) {
        card.innerHTML =
            '<div class="fw-bold small text-uppercase text-muted">Herkomst — Centrale</div>' +
            '<div class="small">' + (label || 'Via kaart') + '</div>' +
            '<div class="text-muted" style="font-size:0.72rem;">' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '</div>';
    }
}

function updateDestCard(label, lat, lon) {
    var card = document.getElementById('destCard');
    if (card) {
        card.innerHTML =
            '<div class="fw-bold small text-uppercase text-muted">Bestemming — Werf</div>' +
            '<div class="small">' + (label || 'Via kaart') + '</div>' +
            '<div class="text-muted" style="font-size:0.72rem;">' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '</div>';
    }
}

function reverseGeocode(lat, lon, type) {
    fetch('/api/search-locations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: lat + ', ' + lon })
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.length > 0) {
            var label = data[0].label;
            if (type === 'origin') {
                document.getElementById('plant_address').value = label;
                updateOriginCard(label, lat, lon);
            } else {
                document.getElementById('site_address').value = label;
                updateDestCard(label, lat, lon);
                var display = document.getElementById('siteAddressDisplay');
                if (display) display.value = label;
            }
        }
    }).catch(function() {});
}

function fetchRoute() {
    var pLat = parseFloat(document.getElementById('plant_lat').value);
    var pLon = parseFloat(document.getElementById('plant_lon').value);
    var sLat = parseFloat(document.getElementById('site_lat').value);
    var sLon = parseFloat(document.getElementById('site_lon').value);

    fetch('/api/route-info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plant_lat: pLat, plant_lon: pLon, site_lat: sLat, site_lon: sLon })
    }).then(function(r) { return r.json(); }).then(function(data) {
        // Draw route
        if (routeLine) map.removeLayer(routeLine);
        if (data.route_coords && data.route_coords.length >= 2) {
            routeLine = L.polyline(data.route_coords, { weight: 5, color: '#2563eb', opacity: 0.85 }).addTo(map);
            map.fitBounds(routeLine.getBounds().pad(0.1));
        }

        // Update distance info
        var distText = document.getElementById('distanceText');
        var sumDist = document.getElementById('sumDistance');
        if (data.source === 'osrm') {
            var msg = '🛣️ Rijafstand: ' + data.distance_km + ' km (≈ ' + data.duration_min + ' min)';
            if (distText) distText.textContent = msg;
            if (sumDist) sumDist.textContent = data.distance_km + ' km';
        } else {
            var msg2 = '📏 Hemelsbreed: ' + data.distance_km + ' km';
            if (distText) distText.textContent = msg2;
            if (sumDist) sumDist.textContent = data.distance_km + ' km';
        }
    }).catch(function() {});
}

// ─── Location Search ─────────────────────────────────────
function searchLocation(type) {
    var queryEl = document.getElementById(type === 'origin' ? 'originQuery' : 'destinationQuery');
    var sugEl = document.getElementById(type === 'origin' ? 'originSuggestions' : 'destSuggestions');
    var query = queryEl ? queryEl.value.trim() : '';
    if (query.length < 3) return;

    fetch('/api/search-locations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query })
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (!data.length) {
            showToast('Geen resultaten gevonden.', 'warning');
            return;
        }
        sugEl.innerHTML = '';
        data.forEach(function(item) {
            var opt = document.createElement('option');
            opt.textContent = item.label;
            opt.dataset.lat = item.lat;
            opt.dataset.lon = item.lon;
            sugEl.appendChild(opt);
        });
        sugEl.style.display = 'block';

        // Auto-select first
        var first = data[0];
        applyLocation(type, first.lat, first.lon, first.label);
    }).catch(function() {
        showToast('Zoekfout.', 'error');
    });
}

// ─── GPS ─────────────────────────────────────────────────
function useGPS(type) {
    if (!navigator.geolocation) {
        showToast('GPS niet beschikbaar in uw browser.', 'warning');
        return;
    }
    navigator.geolocation.getCurrentPosition(
        function(pos) {
            applyLocation(type, pos.coords.latitude, pos.coords.longitude, '');
            reverseGeocode(pos.coords.latitude, pos.coords.longitude, type);
            showToast('📍 Locatie bijgewerkt via GPS', 'success');
        },
        function() {
            showToast('Kan locatie niet ophalen. Geef toestemming in uw browser.', 'warning');
        }
    );
}

// ─── OCR ─────────────────────────────────────────────────
var ocrFields = [];

function runOcrScan() {
    var fileInput = document.getElementById('ocrFile');
    if (!fileInput.files.length) return;

    var spinner = document.getElementById('ocrSpinner');
    var results = document.getElementById('ocrResults');
    spinner.style.display = 'block';
    results.style.display = 'none';

    var formData = new FormData();
    formData.append('file', fileInput.files[0]);

    fetch('/api/ocr-scan', { method: 'POST', body: formData })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            spinner.style.display = 'none';
            if (data.error) {
                showToast('OCR fout: ' + data.error, 'error');
                return;
            }

            ocrFields = data.fields || [];
            if (!ocrFields.length) {
                showToast('Geen velden herkend uit het document.', 'warning');
                return;
            }

            var list = document.getElementById('ocrFieldList');
            list.innerHTML = '';
            ocrFields.forEach(function(f, i) {
                var val = typeof f.value === 'number' ? f.value.toLocaleString() : f.value;
                list.innerHTML +=
                    '<div class="d-flex align-items-center gap-3 py-1 border-bottom">' +
                    '<input type="checkbox" class="form-check-input ocr-check" data-index="' + i + '" checked>' +
                    '<span class="fw-semibold small" style="min-width:180px;">' + f.label + '</span>' +
                    '<span class="small">' + val + '</span>' +
                    '<small class="text-muted ms-auto">' + (f.source || '') + '</small>' +
                    '</div>';
            });
            results.style.display = 'block';
            showToast(ocrFields.length + ' veld(en) herkend!', 'success');
        })
        .catch(function(e) {
            spinner.style.display = 'none';
            showToast('OCR fout: ' + e.message, 'error');
        });
}

function applyOcrFields() {
    var checks = document.querySelectorAll('.ocr-check');
    var applied = 0;
    checks.forEach(function(cb) {
        if (!cb.checked) return;
        var idx = parseInt(cb.dataset.index);
        var f = ocrFields[idx];
        if (!f) return;

        // Map OCR key to form field id
        var key = f.key.replace('k_', '');
        var el = document.getElementById(key) || document.getElementById(f.key);
        if (el) {
            el.value = f.value;
            applied++;
        }
    });
    showToast(applied + ' veld(en) overgenomen!', 'success');
    document.getElementById('ocrResults').style.display = 'none';
}

function clearOcrResults() {
    ocrFields = [];
    document.getElementById('ocrResults').style.display = 'none';
    document.getElementById('ocrFile').value = '';
    document.getElementById('ocrPreview').style.display = 'none';
}

// ─── Weight Calculation ──────────────────────────────────
function updateNetCalc() {
    var bruto = parseFloat(document.getElementById('bruto_kg').value) || 0;
    var tare = parseFloat(document.getElementById('tare_weight_empty_kg').value) || 0;
    var calcEl = document.getElementById('calcNet');
    if (bruto > 0 && tare > 0) {
        var net = (bruto - tare) / 1000.0;
        calcEl.textContent = 'Berekend netto: ' + net.toFixed(2) + ' ton';
    } else {
        calcEl.textContent = '';
    }
}

// ─── Summary Update ──────────────────────────────────────
function updateSummary() {
    var ddn = document.getElementById('delivery_note_no');
    var product = document.getElementById('product_mixture_type');
    var net = document.getElementById('net_total_quantity_ton');

    var sumDdn = document.getElementById('sumDdn');
    var sumProduct = document.getElementById('sumProduct');
    var sumNet = document.getElementById('sumNet');
    var sumRecip = document.getElementById('sumRecipients');

    if (sumDdn) sumDdn.textContent = (ddn && ddn.value.trim()) || '—';
    if (sumProduct) sumProduct.textContent = (product && product.value.trim()) || '—';
    if (sumNet) {
        var nv = parseFloat(net ? net.value : 0) || 0;
        sumNet.textContent = nv > 0 ? nv.toFixed(2) + ' ton' : '—';
    }

    if (sumRecip) {
        var count = 0;
        ['email_client', 'email_transporter', 'email_copro', 'email_permit_holder'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el && el.value.trim()) count++;
        });
        sumRecip.textContent = count;
    }
}

// ─── Demo Data Loader ────────────────────────────────────
function loadDemoData() {
    fetch('/api/demo-data')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // Map API response to form field IDs
            var mapping = {
                delivery_note_no: 'delivery_note_no',
                transport_company: 'transport_company',
                license_plate: 'license_plate',
                product_mixture_type: 'product_mixture_type',
                application: 'application',
                certificate: 'certificate',
                declaration_of_performance: 'declaration_of_performance',
                technical_data_sheet: 'technical_data_sheet',
                mechanical_resistance: 'mechanical_resistance',
                fuel_resistance: 'fuel_resistance',
                deicing_resistance: 'deicing_resistance',
                bitumen_aggregate_affinity: 'bitumen_aggregate_affinity',
                disposal: 'disposal',
                bruto_kg: 'bruto_kg',
                tare_weight_empty_kg: 'tare_weight_empty_kg',
                net_total_quantity_ton: 'net_total_quantity_ton',
                email_client: 'email_client',
                email_transporter: 'email_transporter',
                email_copro: 'email_copro',
                email_permit_holder: 'email_permit_holder',
                energy_source: 'energy_source',
                client_address: 'client_address',
            };

            for (var key in mapping) {
                var el = document.getElementById(mapping[key]);
                if (el && data[key] != null) el.value = data[key];
            }

            // Set origin query
            var oq = document.getElementById('originQuery');
            if (oq && data.origin_query) oq.value = data.origin_query;
            var dq = document.getElementById('destinationQuery');
            if (dq && data.destination_query) dq.value = data.destination_query;

            // Set locations on map
            if (data.plant_lat && data.plant_lon) {
                applyLocation('origin', data.plant_lat, data.plant_lon, data.plant_address || '');
            }
            if (data.site_lat && data.site_lon) {
                applyLocation('destination', data.site_lat, data.site_lon, data.site_address || '');
            }

            updateNetCalc();
            updateSummary();
            showToast('📋 Demogegevens geladen!', 'success');
        })
        .catch(function(e) {
            showToast('Fout bij laden demo: ' + e.message, 'error');
        });
}
