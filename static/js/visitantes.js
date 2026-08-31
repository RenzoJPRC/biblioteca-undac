// FUNCIONES JS AISLADAS DE ADMIN VISITANTES

function inicializarFormularioVisitante() {
    const form = document.getElementById('form-visitante');
    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            const btn = this.querySelector('button[type="submit"]');
            btn.innerHTML = '<i class="ph ph-spinner animate-spin"></i> ...';
            btn.disabled = true;

            const data = {
                dni: document.getElementById('vis-dni').value,
                nombre: document.getElementById('vis-nombre').value,
                institucion: document.getElementById('vis-inst').value,
                correo: document.getElementById('vis-correo').value
            };

            try {
                const res = await fetch('/admin/agregar_visitante', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const responseData = await res.json();

                if (responseData.status === 'success') {
                    if (typeof showToast !== 'undefined') showToast("Visitante registrado con éxito", "success");
                    document.getElementById('modal-nuevo-visitante').classList.add('hidden');
                    form.reset();
                    setTimeout(() => window.location.reload(), 1000);
                } else {
                    if (typeof showToast !== 'undefined') showToast("Error al guardar: " + responseData.msg, "error");
                    else alert("Error al guardar: " + responseData.msg);
                }
            } catch (err) {
                if (typeof showToast !== 'undefined') showToast("Error de conexión al servidor", "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="ph ph-check-circle"></i> Guardar Nuevo';
            }
        });
    }
}

function vaciarBdVisitantes() {
    const code = Math.floor(1000 + Math.random() * 9000);
    const promptStr = prompt(`⚠️ ¡PELIGRO CRÍTICO! ⚠️\nEstás a punto de ELIMINAR TODOS LOS VISITANTES de la base de datos.\nEsta acción borrará los registros permanentemente y NO SE PUEDE RECUPERAR.\n\nEscribe el código "${code}" para autorizar la purga de visitantes:`);

    if (promptStr !== String(code)) {
        alert("Código de seguridad incorrecto. Purga abortada.");
        return;
    }

    fetch('/admin/vaciar_visitantes', {
        method: 'POST'
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                alert(data.msg);
                window.location.reload();
            } else {
                alert('Error: ' + data.msg);
            }
        })
        .catch(err => {
            alert("Ocurrió un error en el servidor.");
            console.error(err);
        });
}

// Helper para cambiar clases
function toggleVisibilidad(id, modoEdicion) {
    const elementos = ['nom', 'dni', 'inst', 'correo'];

    elementos.forEach(el => {
        const span = document.getElementById(`ver-${el}-${id}`);
        const input = document.getElementById(`edit-${el}-${id}`);
        if (span && input) {
            if (modoEdicion) {
                span.classList.add('hidden');
                input.classList.remove('hidden');
            } else {
                span.classList.remove('hidden');
                input.classList.add('hidden');
            }
        }
    });

    const btnEdit = document.getElementById(`btn-editar-${id}`);
    const btnElim = document.getElementById(`btn-eliminar-${id}`);
    const btnsGuardar = document.getElementById(`btns-guardar-${id}`);

    if (btnEdit && btnsGuardar) {
        if (modoEdicion) {
            btnEdit.classList.add('hidden');
            if (btnElim) btnElim.classList.add('hidden');
            btnsGuardar.classList.remove('hidden');
            // Poner foco en el nombre
            const editNom = document.getElementById(`edit-nom-${id}`);
            if (editNom) editNom.focus();
        } else {
            btnEdit.classList.remove('hidden');
            if (btnElim) btnElim.classList.remove('hidden');
            btnsGuardar.classList.add('hidden');
        }
    }
}

function activarEdicion(id) {
    toggleVisibilidad(id, true);
}

function cancelarEdicion(id) {
    toggleVisibilidad(id, false);
}

async function guardarFila(id) {
    const dni = document.getElementById(`edit-dni-${id}`).value;
    const nombre = document.getElementById(`edit-nom-${id}`).value;
    const inst = document.getElementById(`edit-inst-${id}`).value;
    const correo = document.getElementById(`edit-correo-${id}`).value;

    const btnContainer = document.getElementById(`btns-guardar-${id}`);
    btnContainer.innerHTML = '<span class="text-xs text-blue-600 font-bold"><i class="ph ph-spinner animate-spin"></i> Guardando...</span>';

    try {
        const res = await fetch('/admin/editar_visitante', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, dni, nombre, institucion: inst, correo })
        });
        const result = await res.json();

        if (result.status === 'success') {
            document.getElementById(`ver-dni-${id}`).innerText = dni;
            document.getElementById(`ver-nom-${id}`).innerText = nombre;
            document.getElementById(`ver-inst-${id}`).innerText = inst;
            document.getElementById(`ver-correo-${id}`).innerText = correo;
            window.location.reload();
        } else {
            alert("❌ Error: " + result.msg);
            window.location.reload();
        }
    } catch (error) {
        console.error(error);
        alert("❌ Error de conexión al servidor");
        window.location.reload();
    }
}

function eliminarVisitante(id, nombre) {
    if (!confirm(`¿Estás SEGURO de eliminar permanentemente al visitante "${nombre}"? Esta acción borrará también su historial de ingresos pasados.`)) {
        return;
    }

    fetch(`/admin/eliminar_visitante/${id}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                const fila = document.getElementById(`fila-${id}`);
                if (fila) fila.remove();
                if (typeof showToast !== 'undefined') showToast("Visitante eliminado", "success");
            } else {
                if (typeof showToast !== 'undefined') showToast('Error: ' + data.msg, "error");
                else alert('Error: ' + data.msg);
            }
        })
        .catch(err => {
            console.error(err);
            if (typeof showToast !== 'undefined') showToast('Error al intentar conectar con el servidor.', "error");
        });
}
