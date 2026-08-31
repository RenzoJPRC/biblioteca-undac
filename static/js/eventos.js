let currentPage = 1;
let currentQuery = '';

function inicializarEventos() {
    buscarEventos();

    // Auto-update silencioso cada 60s
    setInterval(() => {
        if (!document.getElementById('modal-evento').classList.contains('hidden')) return; // No refrescar si el modal está abierto
        if (!document.getElementById('modal-excel').classList.contains('hidden')) return;
        buscarEventos(true);
    }, 60000);

    const searchInput = document.getElementById('search-input');
    let delayTimer;
    searchInput.addEventListener('input', function () {
        clearTimeout(delayTimer);
        currentQuery = this.value;
        currentPage = 1;
        delayTimer = setTimeout(() => buscarEventos(false), 400);
    });
}

function buscarEventos(silent = false) {
    fetch(`/admin/buscar_eventos?q=${currentQuery}&page=${currentPage}`)
        .then(res => {
            if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
            return res.json();
        })
        .then(data => {
            if (data.status === 'success') {
                renderizarTabla(data.data);
                if (typeof renderizarPaginacion === 'function') {
                    renderizarPaginacion(data.page, data.total_pages, data.total_items);
                } else {
                    console.warn("Falta la función renderizarPaginacion, pero la tabla cargará bien.");
                }
            } else if (!silent) {
                mostrarToastError("Error al cargar eventos: " + data.msg);
            }
        })
        .catch(err => {
            console.error("Fetch Events Error:", err);
            if (!silent) mostrarToastError("Fallo en la carga: " + err.message);
        });
}

function renderizarTabla(eventos) {
    const tbody = document.getElementById('tabla-body');

    if (eventos.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-slate-400 font-medium">No se encontraron eventos.</td></tr>`;
        return;
    }

    let html = "";
    eventos.forEach(evt => {
        let dotColor = 'bg-slate-400';
        let txtEstado = evt.estado_display || evt.estado;

        if (txtEstado === 'En Curso') dotColor = 'bg-emerald-500';
        else if (txtEstado === 'Próximo') dotColor = 'bg-amber-500';
        else if (txtEstado === 'Finalizado') dotColor = 'bg-slate-400';
        else if (evt.estado === 'Cancelado') {
            dotColor = 'bg-rose-500';
            txtEstado = 'Cancelado';
        }

        let groups = [];
        if (evt.permite_alumnos) groups.push('Alu');
        if (evt.permite_egresados) groups.push('Egr');
        if (evt.permite_personal) groups.push('Per');
        if (evt.permite_visitantes) groups.push('Vis');
        if (evt.permite_docentes) groups.push('Doc');
        let txtGrupos = groups.length > 0 ? groups.join(', ') : 'Ninguno';
        if (groups.length === 4) txtGrupos = "Todos autorizados";

        html += `
        <tr class="hover:bg-slate-50 transition-colors group">
            <td class="px-6 py-4 whitespace-nowrap">
                <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-slate-200 bg-white shadow-sm">
                    <span class="w-2 h-2 rounded-full ${dotColor}"></span>
                    ${txtEstado}
                </span>
            </td>
            <td class="px-6 py-4">
                <div class="flex items-start gap-3">
                    <div class="w-10 h-10 rounded-lg bg-rose-50 border border-rose-100 flex items-center justify-center text-rose-500 shrink-0">
                        <i class="ph-bold ph-ticket text-xl"></i>
                    </div>
                    <div>
                        <p class="font-bold text-slate-800 text-base">${evt.nombre}</p>
                        <p class="text-xs text-slate-500 truncate max-w-[200px]"><i class="ph-fill ph-map-pin mr-1"></i>${evt.lugar || 'Sin lugar'}</p>
                    </div>
                </div>
            </td>
            <td class="px-6 py-4">
                <span class="inline-flex items-center justify-center px-2 py-1 rounded text-xs font-bold ${evt.sede === 'Central' ? 'bg-rose-50 text-rose-700' : 'bg-emerald-50 text-emerald-700'} border ${evt.sede === 'Central' ? 'border-rose-100' : 'border-emerald-100'} uppercase">
                    ${evt.sede}
                </span>
            </td>
            <td class="px-6 py-4">
                <p class="text-sm font-bold text-slate-700">${evt.fecha}</p>
                <p class="text-xs text-slate-500 font-mono">${evt.hora_inicio} - ${evt.hora_fin}</p>
            </td>
            <td class="px-6 py-4 text-center">
                <span class="inline-flex items-center justify-center px-3 py-1 rounded-lg bg-sky-50 text-sky-700 font-bold border border-sky-200">
                    <i class="ph-bold ph-users mr-1.5"></i> ${evt.total_asistentes}
                </span>
            </td>
            <td class="px-6 py-4 text-center">
                <button onclick="abrirModalExcel(${evt.id}, '${evt.nombre}')" class="inline-flex items-center justify-center px-3 py-1 rounded-lg bg-orange-50 text-orange-700 font-bold border border-orange-200 hover:bg-orange-100 hover:scale-105 transition-all w-24">
                    <i class="ph-bold ph-upload-simple mr-1.5"></i> ${evt.total_invitados} 
                </button>
            </td>
            <td class="px-6 py-4 text-right whitespace-nowrap">
                <div class="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <a href="/admin/evento_detalle/${evt.id}" class="p-2 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg transition-colors" title="Ver Asistencia">
                        <i class="ph-bold ph-eye text-lg"></i>
                    </a>
                    <button onclick="editarEvento(${evt.id})" class="p-2 text-slate-400 hover:text-sky-600 hover:bg-sky-50 rounded-lg transition-colors" title="Editar">
                        <i class="ph-bold ph-pencil-simple text-lg"></i>
                    </button>
                    <button onclick="confirmarEliminar(${evt.id})" class="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors" title="Eliminar">
                        <i class="ph-bold ph-trash text-lg"></i>
                    </button>
                </div>
            </td>
        </tr>`;
    });

    tbody.innerHTML = html;
}

// === FUNCIONES DEL MODAL ===
function abrirModalNuevo() {
    document.getElementById('form-evento').reset();
    document.getElementById('modal-id').value = '';
    document.getElementById('modal-sede').value = 'Central';
    document.getElementById('modal-titulo').innerHTML = `<i class="ph ph-calendar-plus text-rose-600 text-2xl"></i><span>Nuevo Evento</span>`;

    // Checkboxes default auto
    document.getElementById('chk-alumnos').checked = true;
    document.getElementById('chk-egresados').checked = false;
    document.getElementById('chk-personal').checked = false;
    document.getElementById('chk-visitantes').checked = false;
    document.getElementById('chk-docentes').checked = false;

    document.getElementById('modal-evento').classList.remove('hidden');
    document.getElementById('modal-nombre').focus();
}

function cerrarModal() {
    document.getElementById('modal-evento').classList.add('hidden');
}

window.editarEventoDataTemp = null;
function editarEvento(id) {
    // Buscar datos en la tabla cargada (ahorrando petición)
    fetch(`/admin/buscar_eventos?q=&page=1`)
        .then(res => res.json())
        .then(data => {
            const evts = data.data;
            const evento = evts.find(e => e.id === id);
            if (!evento) return;

            document.getElementById('modal-id').value = evento.id;
            document.getElementById('modal-nombre').value = evento.nombre;
            document.getElementById('modal-fecha').value = evento.fecha_raw;
            document.getElementById('modal-hora-inicio').value = evento.hora_inicio;
            document.getElementById('modal-hora-fin').value = evento.hora_fin;
            document.getElementById('modal-lugar').value = evento.lugar;
            document.getElementById('modal-sede').value = evento.sede || 'Central';

            document.getElementById('chk-alumnos').checked = evento.permite_alumnos;
            document.getElementById('chk-egresados').checked = evento.permite_egresados;
            document.getElementById('chk-personal').checked = evento.permite_personal;
            document.getElementById('chk-visitantes').checked = evento.permite_visitantes;
            document.getElementById('chk-docentes').checked = evento.permite_docentes;

            document.getElementById('modal-titulo').innerHTML = `<i class="ph ph-calendar-edit text-rose-600 text-2xl"></i><span>Editar Evento</span>`;

            document.getElementById('modal-evento').classList.remove('hidden');
        });
}

function guardarEvento(e) {
    e.preventDefault();
    const id = document.getElementById('modal-id').value;

    // Obtener valores de hora directa (Ej: "14:30")
    const data = {
        id: id ? parseInt(id) : null,
        nombre: document.getElementById('modal-nombre').value,
        fecha: document.getElementById('modal-fecha').value,
        hora_inicio: document.getElementById('modal-hora-inicio').value,
        hora_fin: document.getElementById('modal-hora-fin').value,
        lugar: document.getElementById('modal-lugar').value,
        sede: document.getElementById('modal-sede').value,
        permite_alumnos: document.getElementById('chk-alumnos').checked,
        permite_egresados: document.getElementById('chk-egresados').checked,
        permite_personal: document.getElementById('chk-personal').checked,
        permite_visitantes: document.getElementById('chk-visitantes').checked,
        permite_docentes: document.getElementById('chk-docentes').checked
    };

    fetch('/admin/guardar_evento', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
        .then(res => res.json())
        .then(res => {
            if (res.status === 'success') {
                if (typeof mostrarToastExito === 'function') {
                    mostrarToastExito(res.msg);
                } else {
                    alert(res.msg);
                }
                cerrarModal();
                buscarEventos(); // recargar
            } else {
                if (typeof mostrarToastError === 'function') {
                    mostrarToastError(res.msg);
                } else {
                    alert(res.msg);
                }
            }
        })
        .catch(err => {
            console.error("Fetch Error:", err);
            if (typeof mostrarToastError === 'function') {
                mostrarToastError("Error al guardar: " + err.message);
            } else {
                alert("Error al guardar");
            }
        });
}

function confirmarEliminar(id) {
    if (confirm("⚠ ATENCIÓN: ¿Seguro que deseas eliminar este evento? Se borrarán también todos sus registros de asistencia de manera permanente.")) {
        fetch(`/admin/eliminar_evento/${id}`, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    if (typeof mostrarToastExito === 'function') {
                        mostrarToastExito(data.msg);
                    } else {
                        alert(data.msg);
                    }
                    buscarEventos();
                } else {
                    if (typeof mostrarToastError === 'function') {
                        mostrarToastError(data.msg);
                    } else {
                        alert(data.msg);
                    }
                }
            })
            .catch(err => {
                console.error("Fetch Error:", err);
                if (typeof mostrarToastError === 'function') {
                    mostrarToastError("Error al eliminar: " + err.message);
                } else {
                    alert("Error al eliminar");
                }
            });
    }
}

// === ARCHIVOS EXCEL (INVITADOS VIP) ===
function abrirModalExcel(evento_id, nombre_evento) {
    document.getElementById('excel-evento-id').value = evento_id;
    document.getElementById('excel-evento-nombre').innerText = "Destino: " + nombre_evento;
    document.getElementById('archivo-excel').value = '';
    document.getElementById('modal-excel').classList.remove('hidden');
}

function uploadExcel(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-upload');
    const form = document.getElementById('form-excel');
    const input = document.getElementById('archivo-excel');
    const evtId = document.getElementById('excel-evento-id').value;

    if (input.files.length === 0) return;

    btn.innerHTML = `<i class="ph ph-spinner animate-spin"></i> Cargando...`;
    btn.disabled = true;

    const formData = new FormData();
    formData.append('file', input.files[0]);
    formData.append('evento_id', evtId);

    fetch('/admin/importar_invitados_evento', {
        method: 'POST',
        body: formData
    })
        .then(res => res.json())
        .then(data => {
            btn.disabled = false;
            btn.innerHTML = `<i class="ph ph-upload-simple"></i> Cargar Excel`;

            if (data.status === 'success' || data.status === 'processing') {
                mostrarToastExito(data.msg || "Archivo subido correctamente. Procesando invitados...");
                document.getElementById('modal-excel').classList.add('hidden');
                form.reset();

                setTimeout(() => {
                    buscarEventos(true);
                }, 1500);
            } else {
                mostrarToastError(data.msg || "Ocurrió un error al procesar el archivo.");
                alert("Error: " + (data.msg || "Ocurrió un error"));
            }
        })
        .catch(err => {
            btn.disabled = false;
            btn.innerHTML = `<i class="ph ph-upload-simple"></i> Cargar Excel`;
            mostrarToastError("Error en la petición de red");
        });
}

function mostrarToastExito(message) {
    if (typeof showToast === 'function') {
        showToast(message, 'success');
    } else {
        alert(message);
    }
}

function mostrarToastError(message) {
    if (typeof showToast === 'function') {
        showToast(message, 'error');
    } else {
        alert('Error: ' + message);
    }
}

// --- Paginación Global Override ---
function cambiarPagina(page) {
    currentPage = page;
    buscarEventos();
}
