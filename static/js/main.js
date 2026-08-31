let sedeContexto = 'Central';
let previousMenuContext = 'central-menu';

document.addEventListener('DOMContentLoaded', () => {
    // No iniciar el autoverificador de eventos inmediatamente, ya que consume recursos y depende de la sede
    // Se activará cuando abran el menú de eventos
});

function abrirSubMenuFilial(filialName) {
    sedeContexto = filialName;
    document.getElementById('nombre-filial-titulo').innerText = filialName;
    document.getElementById('btn-ingreso-filial').href = "/filial/" + filialName;
    toggleMenu('filial-opciones-menu');
}

function toggleEventosMenu(sede) {
    sedeContexto = sede;
    previousMenuContext = (sede === 'Central') ? 'central-menu' : 'filial-opciones-menu';

    document.getElementById('eventos-back-text').innerText = (sede === 'Central') ? 'Volver a Sede Central' : 'Volver a Filial';
    document.getElementById('eventos-sede-badge').innerText = sede;

    toggleMenu('eventos-menu');
}

function abrirEventosDesdeFilial() {
    toggleEventosMenu(sedeContexto);
}

function cerrarEventosMenu() {
    toggleMenu(previousMenuContext);
    if (window.eventosInterval) {
        clearInterval(window.eventosInterval);
        window.eventosInterval = null;
    }
}

function toggleMenu(menuId) {
    const menus = ['main-menu', 'central-menu', 'filial-menu', 'eventos-menu', 'filial-opciones-menu'];
    menus.forEach(m => {
        const el = document.getElementById(m);
        if (el) {
            el.classList.add('hidden');
        }
    });

    const activeMenu = document.getElementById(menuId);
    if (activeMenu) activeMenu.classList.remove('hidden');

    // Si entra a eventos, recargamos la lista e iniciamos polling
    if (menuId === 'eventos-menu') {
        verificarEventoActivo();
        if (!window.eventosInterval) {
            window.eventosInterval = setInterval(verificarEventoActivo, 60000);
        }
    } else {
        if (window.eventosInterval) {
            clearInterval(window.eventosInterval);
            window.eventosInterval = null;
        }
    }
}

function verificarEventoActivo() {
    fetch('/api/eventos_activos?sede=' + encodeURIComponent(sedeContexto))
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('eventos-container');

            if (data.status === 'success' && data.eventos_activos && data.eventos.length > 0) {
                // Filtrar los que están finalizados para que ya no aparezcan en la vista
                const eventosActivosYProximos = data.eventos.filter(evt => evt.estado_virtual !== 'finalizado');

                if (eventosActivosYProximos.length > 0) {
                    let html = '';
                    eventosActivosYProximos.forEach(evt => {
                        if (evt.estado_virtual === 'en_curso') {
                            // Rojo, animado y con link
                            html += `
                            <a href="/evento/${evt.id}" class="group flex items-center justify-between p-4 bg-white border border-rose-500 rounded-xl transition-all duration-300 shadow-sm cursor-pointer opacity-100 evento-activo-anim hover:text-white">
                                <div class="flex flex-col gap-1 relative z-10 w-full pr-4">
                                    <div class="flex justify-between items-center w-full mb-1">
                                        <span class="bg-rose-100 text-rose-700 text-[10px] font-bold px-2 py-0.5 rounded-full group-hover:bg-white/30 group-hover:text-white transition-colors flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-rose-500 group-hover:bg-white animate-pulse"></span> EN CURSO</span>
                                        <span class="text-xs font-bold text-rose-500 flex items-center gap-1 group-hover:text-white/80 transition-colors"><i class="ph-bold ph-clock"></i> ${evt.hora_inicio} - ${evt.hora_fin}</span>
                                    </div>
                                    <h3 class="font-bold text-lg text-slate-800 transition-colors group-hover:text-white leading-tight">${evt.nombre}</h3>
                                    <span class="text-xs text-slate-500 font-medium transition-colors group-hover:text-white/80 flex items-center gap-1"><i class="ph-fill ph-map-pin"></i> ${evt.lugar}</span>
                                </div>
                                <div class="w-10 h-10 rounded-full bg-rose-50 flex items-center justify-center shrink-0 group-hover:bg-white/20 transition-colors">
                                    <i class="ph-bold ph-caret-right text-rose-500 group-hover:text-white transition-colors text-xl"></i>
                                </div>
                            </a>`;
                        } else if (evt.estado_virtual === 'proximo') {
                            // Grisáceo bloqueado
                            html += `
                            <div class="group flex items-center justify-between p-4 bg-slate-50 border border-slate-200 rounded-xl transition-all duration-300 shadow-sm opacity-80 cursor-not-allowed relative">
                                <div class="flex flex-col gap-1 relative z-10 w-full pr-4">
                                    <div class="flex justify-between items-center w-full mb-1">
                                        <span class="bg-amber-100 text-amber-700 text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1"><i class="ph-bold ph-hourglass-high"></i> PRÓXIMO</span>
                                        <span class="text-xs font-bold text-slate-500 flex items-center gap-1"><i class="ph-bold ph-clock"></i> ${evt.hora_inicio} - ${evt.hora_fin}</span>
                                    </div>
                                    <h3 class="font-bold text-lg text-slate-700 leading-tight">${evt.nombre}</h3>
                                    <span class="text-xs text-slate-400 font-medium flex items-center gap-1"><i class="ph-fill ph-map-pin"></i> ${evt.lugar}</span>
                                </div>
                                <div class="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center shrink-0">
                                    <i class="ph-bold ph-lock-key text-slate-400 text-xl"></i>
                                </div>
                            </div>`;
                        }
                    });

                    container.innerHTML = html;
                } else {
                    renderEmptyAgenda(container);
                }
            } else {
                renderEmptyAgenda(container);
            }
        })
        .catch(error => {
            console.error("Error al consultar agenda:", error);
            document.getElementById('eventos-container').innerHTML = `<div class="col-span-full text-center text-rose-400 text-sm py-8"><i class="ph-fill ph-warning-circle text-xl mb-2"></i><br>Error al cargar agenda</div>`;
        });
}

function renderEmptyAgenda(container) {
    container.innerHTML = `
    <div class="col-span-full flex flex-col items-center justify-center py-12 px-4 bg-white/50 rounded-xl border border-rose-100 border-dashed animate-fade-in-up">
        <i class="ph-fill ph-calendar-blank text-5xl text-rose-200 mb-3"></i>
        <h3 class="text-lg font-bold text-rose-900/60 mb-1">Agenda Vacía</h3>
        <p class="text-sm text-rose-600/50 text-center max-w-sm">No hay ningún evento pendiente o en curso para el día de hoy.</p>
    </div>`;
}
