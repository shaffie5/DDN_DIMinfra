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
                html: '<i class="fas fa-industry" style="color:#2d3748;font-size:24px;"></i>',
                iconSize: [24, 24], className: ''
            }),
        }).addTo(map).bindTooltip('Origin');

        updateOriginCard(label, lat, lon);
    } else {
        document.getElementById('site_lat').value = lat;
        document.getElementById('site_lon').value = lon;
        if (label) document.getElementById('site_address').value = label;

        if (destMarker) map.removeLayer(destMarker);
        destMarker = L.marker([lat, lon], {
            icon: L.divIcon({
                html: '<i class="fas fa-flag-checkered" style="color:#c53030;font-size:24px;"></i>',
                iconSize: [24, 24], className: ''
            }),
        }).addTo(map).bindTooltip('Destination');

        updateDestCard(label, lat, lon);
        var display = document.getElementById('siteAddressDisplay');
        if (display) display.value = label;
    }

    if (originMarker && destMarker) {
        var group = new L.featureGroup([originMarker, destMarker]);
        map.fitBounds(group.getBounds().pad(0.2));
        fetchRoute();
    }

    showToast(type === 'origin' ? 'Origin updated' : 'Destination updated', 'success');
}

function updateOriginCard(label, lat, lon) {
    var card = document.getElementById('originCard');
    if (card) {
        card.innerHTML =
            '<div style="font-weight:600;font-size:0.75rem;text-transform:uppercase;color:var(--text-secondary);">Origin — Plant</div>' +
            '<div style="font-size:0.9rem;">' + (label || 'Via map') + '</div>' +
            '<div style="font-size:0.72rem;color:var(--text-muted);">' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '</div>';
    }
}

function updateDestCard(label, lat, lon) {
    var card = document.getElementById('destCard');
    if (card) {
        card.innerHTML =
            '<div style="font-weight:600;font-size:0.75rem;text-transform:uppercase;color:var(--text-secondary);">Destination — Site</div>' +
            '<div style="font-size:0.9rem;">' + (label || 'Via map') + '</div>' +
            '<div style="font-size:0.72rem;color:var(--text-muted);">' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '</div>';
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
        if (routeLine) map.removeLayer(routeLine);
        if (data.route_coords && data.route_coords.length >= 2) {
            routeLine = L.polyline(data.route_coords, { weight: 5, color: '#2d3748', opacity: 0.85 }).addTo(map);
            map.fitBounds(routeLine.getBounds().pad(0.1));
        }

        var distText = document.getElementById('distanceText');
        var sumDist = document.getElementById('sumDistance');
        if (data.source === 'osrm') {
            var msg = 'Road distance: ' + data.distance_km + ' km (' + data.duration_min + ' min)';
            if (distText) distText.textContent = msg;
            if (sumDist) sumDist.textContent = data.distance_km + ' km';
        } else {
            var msg2 = 'Straight line: ' + data.distance_km + ' km';
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
            showToast('No results found.', 'warning');
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

        var first = data[0];
        applyLocation(type, first.lat, first.lon, first.label);
    }).catch(function() {
        showToast('Search error.', 'error');
    });
}

// ─── GPS ─────────────────────────────────────────────────
function useGPS(type) {
    if (!navigator.geolocation) {
        showToast('GPS not available in your browser.', 'warning');
        return;
    }
    navigator.geolocation.getCurrentPosition(
        function(pos) {
            applyLocation(type, pos.coords.latitude, pos.coords.longitude, '');
            reverseGeocode(pos.coords.latitude, pos.coords.longitude, type);
            showToast('Location updated via GPS', 'success');
        },
        function() {
            showToast('Cannot get location. Please allow permission in your browser.', 'warning');
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
                showToast('OCR error: ' + data.error, 'error');
                return;
            }

            ocrFields = data.fields || [];
            if (!ocrFields.length) {
                showToast('No fields recognised from the document.', 'warning');
                return;
            }

            var list = document.getElementById('ocrFieldList');
            list.innerHTML = '';
            ocrFields.forEach(function(f, i) {
                var val = typeof f.value === 'number' ? f.value.toLocaleString() : f.value;
                list.innerHTML +=
                    '<div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--border);">' +
                    '<input type="checkbox" class="ocr-check" data-index="' + i + '" checked>' +
                    '<span style="font-weight:500;font-size:0.85rem;min-width:180px;">' + f.label + '</span>' +
                    '<span style="font-size:0.85rem;">' + val + '</span>' +
                    '<span style="font-size:0.75rem;color:var(--text-muted);margin-left:auto;">' + (f.source || '') + '</span>' +
                    '</div>';
            });
            results.style.display = 'block';
            showToast(ocrFields.length + ' field(s) recognised!', 'success');
        })
        .catch(function(e) {
            spinner.style.display = 'none';
            showToast('OCR error: ' + e.message, 'error');
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

        var key = f.key.replace('k_', '');
        var el = document.getElementById(key) || document.getElementById(f.key);
        if (el) {
            el.value = f.value;
            applied++;
        }
    });
    showToast(applied + ' field(s) applied!', 'success');
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
        calcEl.textContent = 'Calculated net: ' + net.toFixed(2) + ' ton';
    } else {
        calcEl.textContent = '';
    }
}

// ─── Demo Data Loader ────────────────────────────────────
function loadDemoData() {
    fetch('/api/demo-data')
        .then(function(r) { return r.json(); })
        .then(function(data) {
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

            var oq = document.getElementById('originQuery');
            if (oq && data.origin_query) oq.value = data.origin_query;
            var dq = document.getElementById('destinationQuery');
            if (dq && data.destination_query) dq.value = data.destination_query;

            if (data.plant_lat && data.plant_lon) {
                applyLocation('origin', data.plant_lat, data.plant_lon, data.plant_address || '');
            }
            if (data.site_lat && data.site_lon) {
                applyLocation('destination', data.site_lat, data.site_lon, data.site_address || '');
            }

            updateNetCalc();
            if (typeof updateSummary === 'function') updateSummary();
            showToast('Sample data loaded!', 'success');
        })
        .catch(function(e) {
            showToast('Error loading demo: ' + e.message, 'error');
        });
}
