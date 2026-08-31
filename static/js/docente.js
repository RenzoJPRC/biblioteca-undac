// FUNCIONES JS AISLADAS DE ADMIN PERSONAL
let currentPage = 1;
let totalPages = 1;
let seleccionadosDocente = new Set();

function inicializarAdminDocente() {
    buscarDocente();

    const inputBusqueda = document.getElementById('input-busqueda');
    if (inputBusqueda) {
        inputBusqueda.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') nuevaBusqueda();
        });
    }

    const modalForm = document.getElementById('form-docente');
    if (modalForm) {
        // Remover cualquier submit inline previo, lo manejamos aquí
        modalForm.onsubmit = async function (e) {
            e.preventDefault();
            await submitGuardarDocente(e);
        }
    }

    // Polling silencioso
    setInterval(() => {
        if (!document.getElementById('modal-docente').classList.contains('hidden')) return;
        buscarDocente(currentPage, true);
    }, 60000);
}

function buscarDocente(pagina = 1, silent = false) {
    const inputQ = document.getElementById('input-busqueda');
    const q = inputQ ? inputQ.value : '';
    const btnSearch = document.getElementById('btn-search');

    if (btnSearch && !silent) btnSearch.disabled = true;

    fetch(`/admin/buscar_docentes?q=${encodeURIComponent(q)}&page=${pagina}`)
        .then(res => res.json())
        .then(data => {
            const tbody = document.getElementById('tabla-body');
            if (!tbody) return;
            currentPage = data.pagination ? data.pagination.page : data.page;
            totalPages = data.pagination ? data.pagination.total_pages : data.total_pages;

            const info = document.getElementById('info-paginacion');
            if (info) {
                const totalItems = data.pagination ? data.pagination.total_items : data.total_items;
                info.innerText = `Mostrando ${data.data.length} de ${totalItems} registros`;
            }

            if (data.data.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-slate-400"><i class="ph ph-empty text-3xl mb-2 block"></i>No hay docente encontrado</td></tr>`;
            } else {
                let html = "";
                data.data.forEach(p => {
                    const isSeleccionado = seleccionadosDocente.has(String(p.id));
                    const isAdmin = window.ADMIN_ROL !== 'Consultor';

                    html += `
                        <tr class="hover:bg-slate-50 transition-colors group ${isSeleccionado ? 'bg-purple-50/50' : ''}">
                            ${isAdmin ? `<td class="px-6 py-3 text-center col-acciones">
                                <input type="checkbox" value="${p.id}" onclick="toggleSeleccionDocente(this, '${p.id}')"
                                    ${isSeleccionado ? 'checked' : ''}
                                    class="check-fila-docente w-4 h-4 text-purple-600 bg-slate-100 border-slate-300 rounded focus:ring-purple-500 focus:ring-2 cursor-pointer transition-all">
                            </td>` : ''}
                            <td class="px-6 py-3 font-mono text-slate-500">${String(p.dni).startsWith('INV-') ? '-' : p.dni}</td>
                            <td class="px-6 py-3 font-bold text-slate-700">${p.nombre}</td>
                            <td class="px-6 py-3 text-slate-600">${p.facultad}</td>
                            <td class="px-6 py-3">
                                <div class="flex flex-col gap-0.5 text-[11px] text-slate-500">
                                    ${p.correo_inst ? `<span class="flex items-center gap-1"><i class="ph ph-envelope-simple text-sky-500"></i> ${p.correo_inst}</span>` : ''}
                                    ${p.telefono ? `<span class="flex items-center gap-1"><i class="ph ph-phone text-emerald-500"></i> ${p.telefono}</span>` : ''}
                                </div>
                            </td>
                            ${isAdmin ? `<td class="px-6 py-3 text-right col-acciones">
                                <div class="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button onclick='editarDocente(${JSON.stringify(p).replace(/'/g, "&#39;")})'
                                        class="p-2 text-blue-600 hover:bg-blue-50 rounded-lg transition-colors" title="Editar">
                                        <i class="ph ph-pencil-simple text-lg"></i>
                                    </button>
                                    <button onclick="eliminarDocente(${p.id}, '${p.nombre}')"
                                        class="p-2 text-rose-600 hover:bg-rose-50 rounded-lg transition-colors" title="Eliminar">
                                        <i class="ph ph-trash text-lg"></i>
                                    </button>
                                </div>
                            </td>` : ''}
                        </tr>
                    `;
                });
                tbody.innerHTML = html;
                actualizarEstadoSeccionDocente();
            }
            actualizarControlesPaginacion();
        })
        .catch(err => {
            if (typeof showToast !== 'undefined') showToast("Error cargando docente", "error");
        })
        .finally(() => {
            if (btnSearch) btnSearch.disabled = false;
        });
}

function actualizarControlesPaginacion() {
    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');
    const container = document.getElementById('pagination-numbers');

    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;

    if (!container) return;
    container.innerHTML = '';

    if (totalPages <= 1) return;

    let startPage = Math.max(1, currentPage - 2);
    let endPage = Math.min(totalPages, currentPage + 2);

    const baseColorCls = typeof PAGINATION_COLOR_CLASS !== 'undefined' ? PAGINATION_COLOR_CLASS : 'sky';

    const crearBotonPagina = (num) => {
        const btn = document.createElement('button');
        btn.innerText = num;
        if (num === currentPage) {
            btn.className = `bg-${baseColorCls}-600 text-white w-8 h-8 rounded shrink-0 font-bold shadow-sm transition-colors text-xs`;
        } else {
            btn.className = 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 w-8 h-8 rounded shrink-0 font-medium transition-colors text-xs';
        }
        btn.onclick = () => irAPagina(num);
        return btn;
    };

    if (startPage > 1) {
        container.appendChild(crearBotonPagina(1));
        if (startPage > 2) {
            const dots = document.createElement('span');
            dots.className = 'px-1 text-slate-400 text-xs font-bold cursor-default tracking-widest';
            dots.innerText = '...';
            container.appendChild(dots);
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        container.appendChild(crearBotonPagina(i));
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            const dots = document.createElement('span');
            dots.className = 'px-1 text-slate-400 text-xs font-bold cursor-default tracking-widest';
            dots.innerText = '...';
            container.appendChild(dots);
        }
        container.appendChild(crearBotonPagina(totalPages));
    }
}

function irAPagina(pagina) {
    buscarDocente(parseInt(pagina));
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function cambiarPagina(delta) {
    const nuevaPagina = currentPage + delta;
    if (nuevaPagina >= 1 && nuevaPagina <= Math.max(1, totalPages)) {
        irAPagina(nuevaPagina);
    }
}

function nuevaBusqueda() {
    buscarDocente(1);
}

// --- MODALES Y CRUD ---

function abrirModalNuevo() {
    document.getElementById('form-docente').reset();
    document.getElementById('modal-id').value = '';
    document.getElementById('modal-titulo').innerHTML = '<i class="ph ph-briefcase text-purple-600 text-2xl"></i><span>Nuevo Docente Administrativo</span>';
    document.getElementById('modal-docente').classList.remove('hidden');
}

function cerrarModal() {
    document.getElementById('modal-docente').classList.add('hidden');
}

function editarDocente(p) {
    document.getElementById('form-docente').reset();
    document.getElementById('modal-id').value = p.id;
    document.getElementById('modal-dni').value = p.dni;
    document.getElementById('modal-nombre').value = p.nombre;
    document.getElementById('modal-facultad').value = p.facultad;
    document.getElementById('modal-correo').value = p.correo_docente;
    document.getElementById('modal-correo-inst').value = p.correo_inst;
    document.getElementById('modal-telefono').value = p.telefono;

    document.getElementById('modal-titulo').innerHTML = '<i class="ph ph-pencil-simple text-purple-600 text-2xl"></i><span>Editar Docente</span>';
    document.getElementById('modal-docente').classList.remove('hidden');
}

async function submitGuardarDocente(e) {
    const data = {
        id: document.getElementById('modal-id').value,
        dni: document.getElementById('modal-dni').value,
        nombre: document.getElementById('modal-nombre').value,
        facultad: document.getElementById('modal-facultad').value,
        correo_docente: document.getElementById('modal-correo').value,
        correo_inst: document.getElementById('modal-correo-inst').value,
        telefono: document.getElementById('modal-telefono').value
    };

    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.innerHTML = '<i class="ph ph-spinner animate-spin"></i> Guardando...';

    try {
        const res = await fetch('/admin/guardar_docentes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const resultJSON = await res.json();

        if (resultJSON.status === 'success') {
            cerrarModal();
            if (typeof showToast !== 'undefined') showToast("Docente guardado exitosamente", "success");
            buscarDocente(currentPage);
        } else {
            if (typeof showToast !== 'undefined') showToast(resultJSON.msg, "error");
        }
    } catch (err) {
        console.error(err);
        if (typeof showToast !== 'undefined') showToast("Error de conexión.", "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="ph ph-floppy-disk mr-1"></i> Guardar Docente';
    }
}

function eliminarDocente(id, nombre) {
    if (confirm(`¿Estás seguro de eliminar permanentemente a ${nombre}? Esto borrará todos sus historiales de ingreso también.`)) {
        fetch(`/admin/eliminar_docentes/${id}`, { method: 'DELETE' })
            .then(res => res.json())
            .then(res => {
                if (res.status === 'success') {
                    if (typeof showToast !== 'undefined') showToast("Docente eliminado", "success");
                    buscarDocente(1);
                } else {
                    if (typeof showToast !== 'undefined') showToast(res.msg, "error");
                }
            });
    }
}

// --- ACCIONES MASIVAS E INTERACTIVIDAD PARA PERSONAL ADMINISTRATIVO ---

function toggleSeleccionDocente(checkbox, id) {
    if (checkbox.checked) {
        seleccionadosDocente.add(String(id));
        checkbox.closest('tr').classList.add('bg-purple-50/50');
    } else {
        seleccionadosDocente.delete(String(id));
        checkbox.closest('tr').classList.remove('bg-purple-50/50');
        const checkTodos = document.getElementById('check-todos-docente');
        if (checkTodos) checkTodos.checked = false;
    }
    actualizarBarraFlotanteDocente();
}

function seleccionarTodosDocente(checkboxMaestro) {
    const checkboxes = document.querySelectorAll('.check-fila-docente');
    checkboxes.forEach(cb => {
        cb.checked = checkboxMaestro.checked;
        toggleSeleccionDocente(cb, cb.value);
    });
}

function actualizarEstadoSeccionDocente() {
    const checkboxes = document.querySelectorAll('.check-fila-docente');
    if (checkboxes.length === 0) return;

    const todosSeleccionados = Array.from(checkboxes).every(cb => cb.checked);
    const checkTodos = document.getElementById('check-todos-docente');
    if (checkTodos) checkTodos.checked = todosSeleccionados;
    actualizarBarraFlotanteDocente();
}

function actualizarBarraFlotanteDocente() {
    const barra = document.getElementById('barra-acciones-docente');
    const contador = document.getElementById('contador-seleccionados');
    if (!barra || !contador) return;

    if (seleccionadosDocente.size > 0) {
        contador.innerText = seleccionadosDocente.size;
        barra.classList.remove('hidden');
    } else {
        barra.classList.add('hidden');
    }
}

function accionMasivaDocente(accion) {
    if (seleccionadosDocente.size === 0) return;

    let confirmMsg = `¿Seguro que quieres ${accion.toUpperCase()} a los ${seleccionadosDocente.size} registros seleccionados?`;
    if (accion === 'eliminar') {
        confirmMsg = `⚠️ ¡ADVERTENCIA! ¿Estás SEGURO de ELIMINAR permanentemente a los ${seleccionadosDocente.size} registros de docente administrativo seleccionados?\nEsta acción es irreversible e impactará sus registros de visitas vinculados.`;
    }

    if (!confirm(confirmMsg)) return;

    const ids = Array.from(seleccionadosDocente);

    fetch(`/admin/eliminar_docentes_masivo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                seleccionadosDocente.clear();
                actualizarBarraFlotanteDocente();
                document.getElementById('check-todos-docente').checked = false;
                if (typeof showToast !== 'undefined') showToast(data.msg, "success");
                buscarDocente(currentPage);
            } else {
                alert('Error: ' + data.msg);
            }
        })
        .catch(err => {
            console.error(err);
            alert('Error de conexión con el servidor.');
        });
}

function accionGlobalDocente(accion) {
    if (accion === 'vaciar') {
        const code = Math.floor(1000 + Math.random() * 9000);
        const promptStr = prompt(`⚠️ ¡PELIGRO CRÍTICO! ⚠️\nEstás a punto de VACIAR TODA LA TABLA DE PERSONAL ADMINISTRATIVO de la base de datos central de la UNDAC.\n\nEsto borrará cientos de registros y no se puede recuperar.\n\nEscribe el código "${code}" para autorizar la destrucción masiva de la tabla:`);

        if (promptStr !== String(code)) {
            alert("Código de confirmación incorrecto. Destrucción abortada.");
            return;
        }

        fetch('/admin/vaciar_docentes', {
            method: 'DELETE'
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    alert("✅ " + data.msg);
                    seleccionadosDocente.clear();
                    buscarDocente(1);
                } else {
                    alert("Error crítico: " + data.msg);
                }
            })
            .catch(err => alert('Fallo de conexión crítico.'));
    }
}
