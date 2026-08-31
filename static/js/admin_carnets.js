let currentPage = 1;
let totalPages = 1;
let selectedIds = new Set(); // Almacena IDs seleccionados globalmente

// --- INICIALIZACIÓN ---
window.onload = function () {
    buscarAlumno();

    // Auto-update silencioso cada 60s
    setInterval(() => {
        if (!document.getElementById('modal-editar').classList.contains('hidden')) return;
        buscarAlumno(currentPage, true);
    }, 60000);
};

// --- BÚSQUEDA Y RENDERIZADO ---
function buscarAlumno(pagina = 1, silent = false) {
    const query = document.getElementById('input-busqueda').value;
    currentPage = pagina;

    const url = `/admin/buscar_alumno?q=${query}&page=${currentPage}`;

    // NO reseteamos selectedIds aquí para mantener selección entre búsquedas/páginas
    // Solo reseteamos el check "master" visual si existe
    const checkTodos = document.getElementById('check-todos');
    if (checkTodos) checkTodos.checked = false;

    // Indicador de carga
    const info = document.getElementById('info-paginacion');
    if (!silent) info.innerText = "Cargando...";

    fetch(url)
        .then(res => res.json())
        .then(resp => {
            const tbody = document.getElementById('tabla-body');

            const data = resp.data;
            const meta = resp.pagination;

            // Actualizar variables
            currentPage = meta.page;
            totalPages = meta.total_pages;

            if (meta.total_items === 0) {
                info.innerText = "0 Resultados";
                tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-12 text-center text-slate-400 font-medium">No se encontraron alumnos</td></tr>';
                actualizarControlesPaginacion();
                actualizarContador(); // Actualizar barra con el total global
                return;
            }

            info.innerText = `Mostrando ${data.length} de ${meta.total_items} alumnos`;

            let html = "";
            data.forEach(alumno => {
                const isActivo = alumno.estado === 'ACTIVO';
                const estadoHtml = isActivo
                    ? `<span class="bg-emerald-100 text-emerald-700 px-2 py-1 rounded text-xs font-bold">ACTIVO</span>`
                    : `<span class="bg-rose-100 text-rose-700 px-2 py-1 rounded text-xs font-bold">VENCIDO</span>`;

                const fechaManual = alumno.fecha_manual
                    ? alumno.fecha_manual
                    : '<span class="text-slate-400 italic">Auto</span>';

                // Verificar si está seleccionado
                const isChecked = selectedIds.has(String(alumno.id)) ? 'checked' : '';

                const isAdmin = window.ADMIN_ROL !== 'Consultor';

                html += `
                    <tr class="hover:bg-slate-50 transition-colors group">
                    ${isAdmin ? `<td class="px-6 py-3 text-center border-b border-slate-50 relative col-acciones">
                        <input type="checkbox" value="${alumno.id}" onchange="toggleIndividual(this)" ${isChecked}
                               class="check-alumno w-4 h-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500 cursor-pointer">
                    </td>` : ''}
                    <td class="px-6 py-3 font-medium text-slate-700 border-b border-slate-50">${alumno.nombre}</td>
                    <td class="px-6 py-3 border-b border-slate-50">
                        <div class="text-slate-600">${alumno.dni}</div>
                        <div class="text-xs text-slate-400 font-mono">${alumno.codigo}</div>
                    </td>
                    <td class="px-6 py-3 text-slate-500 text-xs border-b border-slate-50">${alumno.escuela}</td>
                    <td class="px-6 py-3 font-mono text-xs border-b border-slate-50">${fechaManual}</td>
                    <td class="px-6 py-3 border-b border-slate-50">${estadoHtml}</td>
                    ${isAdmin ? `<td class="px-6 py-3 text-center border-b border-slate-50 relative col-acciones">
                        <div class="flex justify-center gap-1 opacity-100 transition-opacity">
                            <button onclick='abrirModal(${JSON.stringify(alumno).replace(/'/g, "&#39;")})' 
                                    class="text-slate-400 hover:text-sky-600 p-2 rounded-full hover:bg-sky-50 transition-all" title="Editar Alumno">
                                <i class="ph ph-pencil-simple text-lg"></i>
                            </button>
                            <button onclick="eliminarAlumno(${alumno.id}, '${alumno.nombre}')" 
                                    class="text-slate-400 hover:text-rose-600 p-2 rounded-full hover:bg-rose-50 transition-all font-bold" title="Eliminar Alumno">
                                <i class="ph ph-trash text-lg"></i>
                            </button>
                        </div>
                    </td>` : ''}
                </tr>`;
            });

            tbody.innerHTML = html;

            actualizarControlesPaginacion();
            actualizarContador(); // Actualizar barra con el total global
        })
        .catch(err => console.error("Error cargando alumnos:", err));
}

// --- PAGINACIÓN UI ---
function actualizarControlesPaginacion() {
    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');
    const container = document.getElementById('pagination-numbers');

    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;

    if (!container) return;
    container.innerHTML = '';

    if (totalPages <= 1) return; // Ocultar si hay 1 sola página

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

function cambiarPagina(delta) {
    const nuevaPagina = currentPage + delta;
    if (nuevaPagina >= 1 && nuevaPagina <= totalPages) {
        irAPagina(nuevaPagina);
    }
}

function irAPagina(pagina) {
    buscarAlumno(parseInt(pagina));
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Wrapper para el botón de buscar (resetea a pagina 1)
function nuevaBusqueda() {
    buscarAlumno(1);
}

// --- GESTIÓN DE SELECCIÓN (Checkboxes) ---

function toggleIndividual(checkbox) {
    const id = checkbox.value;
    if (checkbox.checked) {
        selectedIds.add(id);
    } else {
        selectedIds.delete(id);
        document.getElementById('check-todos').checked = false; // Desmarcar master si uno se desmarca
    }
    actualizarContador();
}

function toggleTodos() {
    const master = document.getElementById('check-todos');
    const checks = document.querySelectorAll('.check-alumno'); // Solo los visibles

    checks.forEach(c => {
        c.checked = master.checked;
        const id = c.value;
        if (master.checked) {
            selectedIds.add(id);
        } else {
            selectedIds.delete(id);
        }
    });
    actualizarContador();
}

function actualizarContador() {
    const totalSeleccionados = selectedIds.size;
    const barra = document.getElementById('barra-acciones');
    const contador = document.getElementById('contador-seleccionados');

    contador.innerText = totalSeleccionados;

    if (totalSeleccionados > 0) barra.classList.remove('hidden');
    else barra.classList.add('hidden');
}

// --- ACCIONES MASIVAS ---

function accionMasiva(accion) {
    const ids = Array.from(selectedIds);

    if (ids.length === 0) return;
    if (!confirm(`¿Estás seguro de aplicar esta acción a ${ids.length} alumnos seleccionados?`)) return;

    fetch('/admin/actualizar_carnet_masivo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, accion: accion })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                alert(data.msg);
                selectedIds.clear(); // Limpiar selección tras acción exitosa
                actualizarContador();
                buscarAlumno(currentPage); // Recargar página actual
            } else {
                alert('Error: ' + data.msg);
            }
        });
}

function eliminarSeleccionados() {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    if (!confirm(`⚠️ ALERTA DE BORRADO DE DATOS:\n\nEstás a punto de ELIMINAR PERMANENTEMENTE el registro y carnet de ${ids.length} alumnos.\nEsta acción destruirá su acceso al código de barras.\n\n¿Deseas proceder de todos modos?`)) return;

    fetch('/admin/eliminar_alumnos_masivo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                alert('Alumnos ELIMINADOS con éxito.');
                selectedIds.clear();
                actualizarContador();
                document.getElementById('check-todos').checked = false;
                buscarAlumno(currentPage);
            } else {
                alert('Error: ' + data.msg);
            }
        })
        .catch(err => console.error(err));
}

// --- ACCIONES GLOBALES (Toda la Base de Datos) ---
function accionGlobal(accion) {
    const accionTexto = accion === 'activar' ? 'ACTIVAR' : 'DESACTIVAR';

    if (!confirm(`⚠️ ATENCIÓN: Estás a punto de ${accionTexto} a TODOS los alumnos registrados en la base de datos.\n\nEsta alteración aplicará globalmente ignorando las páginas actuales.\n\n¿Estás completamente seguro de continuar?`)) return;

    // Opcional: mostrar un indicador visual de que la operación global está en marcha
    const info = document.getElementById('info-paginacion');
    info.innerText = "Procesando operación masiva en la BD...";

    fetch('/admin/actualizar_carnet_global', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accion: accion })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                alert(data.msg);
                selectedIds.clear(); // Limpiar selecciones activas
                document.getElementById('check-todos').checked = false;
                actualizarContador();
                buscarAlumno(currentPage); // Recargar la vista para ver los cambios
            } else {
                alert('Error: ' + data.msg);
                info.innerText = "Error en la operación global";
            }
        })
        .catch(err => {
            console.error("Error en accion global:", err);
            alert("Ocurrió un error de red o de servidor.");
            info.innerText = "Error local";
        });
}

// --- MODAL DE EDICIÓN DE ALUMNO ---
function abrirModal(alumno) {
    document.getElementById('modal-alumno-id').value = alumno.id || '';
    document.getElementById('modal-nombre').value = alumno.nombre || '';

    // Si la DB mandó INV- lo mostramos vacío para edición
    let dVal = String(alumno.dni || '');
    document.getElementById('modal-dni').value = dVal.startsWith('INV-') ? '' : dVal;

    document.getElementById('modal-codigo').value = alumno.codigo || '';
    document.getElementById('modal-escuela').value = alumno.escuela || '';
    document.getElementById('modal-fecha').value = alumno.fecha_manual || '';

    document.getElementById('modal-editar').classList.remove('hidden');
}

function cerrarModal() {
    document.getElementById('modal-editar').classList.add('hidden');
}

function guardarAlumnoCompleto() {
    const data = {
        id: document.getElementById('modal-alumno-id').value,
        nombre: document.getElementById('modal-nombre').value,
        dni: document.getElementById('modal-dni').value,
        codigo: document.getElementById('modal-codigo').value,
        escuela: document.getElementById('modal-escuela').value,
        fecha: document.getElementById('modal-fecha').value
    };

    fetch('/admin/actualizar_alumno_completo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
        .then(res => res.json())
        .then(resData => {
            if (resData.status === 'success') {
                if (typeof showToast !== 'undefined') showToast("Alumno actualizado con éxito", "success");
                cerrarModal();
                buscarAlumno(currentPage);
            } else {
                if (typeof showToast !== 'undefined') showToast('Error: ' + resData.msg, "error");
                else alert('Error: ' + resData.msg);
            }
        })
        .catch(err => console.error(err));
}

function abrirModalNuevo() {
    document.getElementById('nuevo-nombre').value = '';
    document.getElementById('nuevo-dni').value = '';
    document.getElementById('nuevo-codigo').value = '';
    document.getElementById('nuevo-escuela').value = '';
    document.getElementById('nuevo-fecha').value = '';
    document.getElementById('modal-nuevo').classList.remove('hidden');
}

function cerrarModalNuevo() {
    document.getElementById('modal-nuevo').classList.add('hidden');
}

async function buscarAlumnoUNDAC() {
    const codigoInput = document.getElementById('nuevo-codigo');
    const codigo = codigoInput.value.trim();
    
    if (!codigo) {
        if (typeof showToast !== 'undefined') showToast("Escribe un código primero", "error");
        else alert("Escribe un código primero");
        return;
    }
    
    // Feedback visual
    const btnIcon = codigoInput.nextElementSibling.querySelector('i');
    btnIcon.className = "ph-bold ph-spinner animate-spin";
    
    try {
        const response = await fetch(`/api/undac/alumno/${codigo}`);
        const data = await response.json();
        
        if (data.status === 'success') {
            document.getElementById('nuevo-nombre').value = data.data.nombre_completo;
            document.getElementById('nuevo-dni').value = data.data.dni;
            document.getElementById('nuevo-escuela').value = data.data.facultad;
            
            if (typeof showToast !== 'undefined') showToast("Datos autocompletados", "success");
        } else {
            if (typeof showToast !== 'undefined') showToast(data.msg, "error");
            else alert(data.msg);
        }
    } catch (error) {
        if (typeof showToast !== 'undefined') showToast("Error de conexión con el servidor", "error");
        console.error(error);
    } finally {
        btnIcon.className = "ph-bold ph-magnifying-glass";
    }
}

function guardarNuevoAlumno() {
    const data = {
        nombre: document.getElementById('nuevo-nombre').value,
        dni: document.getElementById('nuevo-dni').value,
        codigo: document.getElementById('nuevo-codigo').value,
        escuela: document.getElementById('nuevo-escuela').value,
        fecha: document.getElementById('nuevo-fecha').value
    };

    fetch('/admin/crear_alumno', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
        .then(res => res.json())
        .then(resData => {
            if (resData.status === 'success') {
                if (typeof showToast !== 'undefined') showToast("Alumno creado con éxito", "success");
                cerrarModalNuevo();
                buscarAlumno(1); // Carga la primera página para que vea su registro
            } else {
                if (typeof showToast !== 'undefined') showToast('Error: ' + resData.msg, "error");
                else alert('Error: ' + resData.msg);
            }
        })
        .catch(err => console.error(err));
}

function eliminarAlumno(id, nombre) {
    if (!confirm(`¿Estás SEGURO de eliminar permanentemente al alumno "${nombre}"? Esta acción borrará también su historial.`)) {
        return;
    }

    fetch(`/admin/eliminar_alumno/${id}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                if (typeof showToast !== 'undefined') showToast("Alumno eliminado", "success");
                buscarAlumno(currentPage);
            } else {
                if (typeof showToast !== 'undefined') showToast('Error: ' + data.msg, "error");
                else alert('Error: ' + data.msg);
            }
        })
        .catch(err => console.error(err));
}

function vaciarBdAlumnos() {
    if (!confirm("⚠️ ¡ADVERTENCIA EXTREMA!\n\n¿Estás SEGURO de VACIAR permanentemente TODA la tabla de Alumnos y Carnets?\nEsta acción es irreversible y borrará a miles de estudiantes.\n\nEscribe 'CONFIRMAR' para proceder:")) return;

    const validacion = prompt("Escribe CONFIRMAR para vaciar la base de datos de Alumnos:");
    if (validacion !== "CONFIRMAR") {
        alert("Operación cancelada. Escribiste incorrectamente.");
        return;
    }

    fetch(`/admin/vaciar_alumnos`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                alert("La base de datos de Alumnos ha sido purgada completamente.");
                window.location.reload();
            } else {
                alert('Error al vaciar BD: ' + data.msg);
            }
        })
        .catch(err => console.error(err));
}

// Buscador con Enter
document.getElementById('input-busqueda').addEventListener('keypress', function (e) {
    if (e.key === 'Enter') nuevaBusqueda();
});
