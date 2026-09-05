<script>
  import { onMount } from 'svelte';

  let { language = 'es', onConnectionChange = () => {} } = $props();

  const copy = {
    es: { mine: 'Mi estación', favourites: 'Favoritos', connected: 'Estación conectada', empty: 'Todavía no has guardado ninguna estación.', other: 'Conectar otra estación', save: 'Guardar favorito', saved: 'Guardado', disconnect: 'Desconectar', station: 'ID de estación', key: 'API key', secret: 'API secret', elevation: 'Altitud (m)', remember: 'Recordar en este navegador', connect: 'Conectar', find: 'Buscar mis estaciones', searching: 'Buscando…', rejected: 'No se pudieron consultar las estaciones.' },
    ca: { mine: 'La meva estació', favourites: 'Preferits', connected: 'Estació connectada', empty: 'Encara no has desat cap estació.', other: 'Connectar una altra estació', save: 'Desar favorit', saved: 'Desat', disconnect: 'Desconnectar', station: "ID d'estació", key: 'API key', secret: 'API secret', elevation: 'Altitud (m)', remember: 'Recordar en aquest navegador', connect: 'Connectar', find: 'Cercar les meves estacions', searching: 'Cercant…', rejected: "No s'han pogut consultar les estacions." },
    en: { mine: 'My station', favourites: 'Favourites', connected: 'Connected station', empty: 'No saved stations yet.', other: 'Connect another station', save: 'Save favourite', saved: 'Saved', disconnect: 'Disconnect', station: 'Station ID', key: 'API key', secret: 'API secret', elevation: 'Elevation (m)', remember: 'Remember in this browser', connect: 'Connect', find: 'Find my stations', searching: 'Searching…', rejected: 'The stations could not be queried.' },
    fr: { mine: 'Ma station', favourites: 'Favoris', connected: 'Station connectée', empty: "Vous n'avez encore enregistré aucune station.", other: 'Connecter une autre station', save: 'Ajouter aux favoris', saved: 'Enregistrée', disconnect: 'Déconnecter', station: 'ID de station', key: 'Clé API', secret: 'Secret API', elevation: 'Altitude (m)', remember: 'Mémoriser dans ce navigateur', connect: 'Connecter', find: 'Trouver mes stations', searching: 'Recherche…', rejected: "Impossible de consulter les stations." },
    it: { mine: 'La mia stazione', favourites: 'Preferiti', connected: 'Stazione connessa', empty: 'Non hai ancora salvato alcuna stazione.', other: "Connetti un'altra stazione", save: 'Salva preferito', saved: 'Salvata', disconnect: 'Disconnetti', station: 'ID stazione', key: 'Chiave API', secret: 'Segreto API', elevation: 'Altitudine (m)', remember: 'Ricorda in questo browser', connect: 'Connetti', find: 'Trova le mie stazioni', searching: 'Ricerca…', rejected: 'Impossibile consultare le stazioni.' },
    pt: { mine: 'A minha estação', favourites: 'Favoritos', connected: 'Estação ligada', empty: 'Ainda não guardou nenhuma estação.', other: 'Ligar outra estação', save: 'Guardar favorito', saved: 'Guardada', disconnect: 'Desligar', station: 'ID da estação', key: 'Chave API', secret: 'Segredo API', elevation: 'Altitude (m)', remember: 'Guardar neste navegador', connect: 'Ligar', find: 'Procurar as minhas estações', searching: 'A procurar…', rejected: 'Não foi possível consultar as estações.' }
  };
  const labels = $derived(copy[language] || copy.es);

  let mineMenu;
  let stationMenu;
  let connection = $state(null);
  let favourites = $state([]);
  let network = $state('WU');
  let stationId = $state('');
  let apiKey = $state('');
  let apiSecret = $state('');
  let elevation = $state('');
  let remember = $state(true);
  let stations = $state([]);
  let loading = $state(false);
  let error = $state('');

  function read(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value ? JSON.parse(value) : fallback;
    } catch { return fallback; }
  }

  function write(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch { /* almacenamiento bloqueado */ }
  }

  function refresh() {
    connection = read('mlx-connection', null);
    const saved = read('mlx-favourites', []);
    favourites = Array.isArray(saved) ? saved.filter((item) => item?.slug || item?.path) : [];
    // Un enlace directo a Predicción puede traer el slug en la URL aunque
    // este navegador aún no tenga conexión recordada. En ese caso no se pisa.
    if (connection) onConnectionChange(connection);
  }

  onMount(() => {
    refresh();
    const credentials = { ...read('mlx-credentials', {}), ...(() => {
      try { return JSON.parse(sessionStorage.getItem('mlx-credentials') || '{}'); } catch { return {}; }
    })() };
    const wu = credentials.WU || {};
    stationId = wu.stationId || '';
    apiKey = wu.apiKey || '';
    elevation = Number.isFinite(wu.elevation) ? String(wu.elevation) : '';
    const sync = () => refresh();
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  });

  const keyOf = (item) => item?.slug || item?.path || '';
  const currentKey = $derived(keyOf(connection));
  const isSaved = $derived(Boolean(currentKey) && favourites.some((item) => keyOf(item) === currentKey));
  const hrefOf = (item) => item?.slug ? `/${language}/observation/${item.slug}` : (item?.path || '/');

  function toggleFavourite(item) {
    if (!item) return;
    const key = keyOf(item);
    const without = favourites.filter((entry) => keyOf(entry) !== key);
    favourites = without.length === favourites.length
      ? [...without, { slug: item.slug || '', path: item.path || '', name: item.name || '', provider: item.provider || '' }]
      : without;
    write('mlx-favourites', favourites);
  }

  function disconnect() {
    connection = null;
    write('mlx-connection', null);
    onConnectionChange(null);
  }

  function height() {
    const value = Number(String(elevation).replace(',', '.'));
    return Number.isFinite(value) ? value : null;
  }

  function saveCredential(provider, value) {
    const target = remember ? localStorage : sessionStorage;
    const other = remember ? sessionStorage : localStorage;
    try {
      const existing = JSON.parse(target.getItem('mlx-credentials') || '{}');
      target.setItem('mlx-credentials', JSON.stringify({ ...existing, [provider]: value }));
      const stale = JSON.parse(other.getItem('mlx-credentials') || '{}');
      delete stale[provider];
      if (Object.keys(stale).length) other.setItem('mlx-credentials', JSON.stringify(stale));
      else other.removeItem('mlx-credentials');
    } catch { /* la conexión seguirá funcionando durante la navegación */ }
  }

  function connectWu(event) {
    event.preventDefault();
    const id = stationId.trim();
    if (!id || !apiKey.trim()) return;
    saveCredential('WU', { stationId: id, apiKey: apiKey.trim(), elevation: height() });
    location.href = `/${language}/observation/WU/${encodeURIComponent(id)}`;
  }

  async function findWeatherLink(event) {
    event.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) return;
    loading = true; error = ''; stations = [];
    try {
      const response = await fetch('/v1/stations/weatherlink', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey.trim(), api_secret: apiSecret.trim() })
      });
      if (!response.ok) throw new Error('failed');
      const payload = await response.json();
      stations = Array.isArray(payload?.stations) ? payload.stations : [];
    } catch { error = labels.rejected; }
    finally { loading = false; }
  }

  function connectWeatherLink(station) {
    const id = String(station.station_id);
    const ownHeight = Number(station.elevation ?? station.elevation_m);
    saveCredential('WEATHERLINK', { stationId: id, apiKey: apiKey.trim(), apiSecret: apiSecret.trim(), elevation: Number.isFinite(ownHeight) ? ownHeight : height() });
    location.href = `/${language}/observation/WEATHERLINK/${encodeURIComponent(id)}`;
  }

  function closeOutside(event) {
    if (mineMenu?.open && !mineMenu.contains(event.target)) mineMenu.open = false;
    if (stationMenu?.open && !stationMenu.contains(event.target)) stationMenu.open = false;
  }
</script>

<svelte:window onpointerdown={closeOutside} onkeydown={(event) => { if (event.key === 'Escape') { if (mineMenu) mineMenu.open = false; if (stationMenu) stationMenu.open = false; } }} />

<details class="mine" bind:this={mineMenu}>
  <summary title={labels.mine}><span aria-hidden="true">+</span><span class="txt">{labels.mine}</span></summary>
  <div class="panel connect-panel">
    <div class="network-tabs">
      <button type="button" class:on={network === 'WU'} onclick={() => { network = 'WU'; apiSecret = ''; }}>Weather Underground</button>
      <button type="button" class:on={network === 'WEATHERLINK'} onclick={() => { network = 'WEATHERLINK'; stationId = ''; }}>WeatherLink</button>
    </div>
    <form onsubmit={network === 'WU' ? connectWu : findWeatherLink}>
      {#if network === 'WU'}<label><span>{labels.station}</span><input bind:value={stationId} placeholder="IBARCE12345" autocomplete="off" /></label>{/if}
      <label><span>{labels.key}</span><input bind:value={apiKey} type="password" autocomplete="off" /></label>
      {#if network === 'WEATHERLINK'}<label><span>{labels.secret}</span><input bind:value={apiSecret} type="password" autocomplete="off" /></label>{/if}
      <label><span>{labels.elevation}</span><input bind:value={elevation} inputmode="numeric" placeholder="120" /></label>
      <label class="remember"><input type="checkbox" bind:checked={remember} /><span>{labels.remember}</span></label>
      <button class="primary" type="submit" disabled={loading}>{network === 'WU' ? labels.connect : loading ? labels.searching : labels.find}</button>
    </form>
    {#if error}<p class="error">{error}</p>{/if}
    {#if stations.length}<ul>{#each stations as station (station.station_id)}<li><button type="button" onclick={() => connectWeatherLink(station)}><strong>{station.station_name || station.station_id}</strong><small>{station.station_id}</small></button></li>{/each}</ul>{/if}
  </div>
</details>

<details class="station" bind:this={stationMenu}>
  <summary title={labels.favourites}>
    <svg viewBox="0 0 24 24" aria-hidden="true" class:filled={isSaved}><path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8Z" /></svg>
    {#if connection?.name}<span class="who">{connection.name}</span>{/if}
  </summary>
  <div class="panel favourites-panel">
    {#if connection}
      <div class="current"><small>{labels.connected}</small><strong>{connection.name || connection.slug}</strong><div class="actions"><button type="button" onclick={() => toggleFavourite(connection)}>{isSaved ? labels.saved : labels.save}</button><a href="/" onclick={disconnect}>{labels.disconnect}</a></div></div>
    {/if}
    <div class="list-head">{labels.favourites}</div>
    {#if favourites.length}<ul>{#each favourites as favourite (keyOf(favourite))}<li><a href={hrefOf(favourite)}><strong>{favourite.name || favourite.slug}</strong><small>{favourite.provider || ''}</small></a><button class="remove" type="button" aria-label="×" onclick={() => toggleFavourite(favourite)}>×</button></li>{/each}</ul>{:else}<p class="empty">{labels.empty}</p>{/if}
    <a class="other" href={`/${language}`}>{labels.other}</a>
  </div>
</details>

<style>
  details{position:relative}summary{display:inline-flex;align-items:center;gap:7px;max-width:210px;padding:5px 10px 5px 8px;border:1px solid var(--border);border-radius:9px;background:var(--card);color:var(--muted);font-size:.74rem;font-weight:600;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary:hover{color:var(--ink);border-color:var(--border-2)}summary svg{width:15px;height:15px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linejoin:round}summary svg.filled{fill:var(--accent);stroke:var(--accent)}.who{overflow:hidden;color:var(--ink-2);text-overflow:ellipsis;white-space:nowrap}.panel{position:absolute;z-index:45;top:calc(100% + 7px);right:0;width:min(340px,calc(100vw - 28px));padding:12px;border:1px solid var(--border-2);border-radius:12px;background:var(--panel);box-shadow:var(--shadow)}.network-tabs{display:flex;gap:3px;padding:3px;margin-bottom:10px;border-radius:9px;background:var(--panel-2)}.network-tabs button{flex:1;padding:6px 8px;border:0;border-radius:7px;color:var(--muted);background:transparent;font-size:.68rem;font-weight:650}.network-tabs button.on{color:var(--ink);background:var(--card)}form{display:flex;flex-direction:column;gap:8px}label{display:flex;flex-direction:column;gap:3px}label span{color:var(--muted);font-size:.66rem;font-weight:650}input{padding:7px 9px;border:1px solid var(--border);border-radius:8px;color:var(--ink);background:var(--panel-2);font:inherit;font-size:.78rem}input:focus{outline:none;border-color:var(--accent)}.remember{align-items:center;flex-direction:row;gap:7px}.remember input{width:14px;height:14px;padding:0}.primary{align-self:flex-start;padding:7px 14px;border:1px solid transparent;border-radius:8px;color:#fff;background:var(--accent);font-size:.74rem;font-weight:700}.error,.empty{padding:8px 9px;color:var(--muted);font-size:.72rem}.current{display:flex;flex-direction:column;gap:6px;padding:8px 9px 10px;margin-bottom:8px;border-bottom:1px solid var(--border)}.current>small,.list-head{color:var(--muted-2);font-size:.62rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase}.current>strong{overflow:hidden;color:var(--ink);font-size:.82rem;text-overflow:ellipsis;white-space:nowrap}.actions{display:flex;justify-content:flex-end;gap:6px}.actions button,.actions a{padding:4px 8px;border:1px solid var(--border);border-radius:7px;color:var(--muted);background:var(--card);font-size:.66rem;font-weight:600;text-decoration:none}.list-head{padding:2px 9px 6px}ul{display:flex;max-height:240px;flex-direction:column;gap:2px;margin:0;padding:0;overflow-y:auto;list-style:none}li{display:flex;align-items:center;gap:6px;border-radius:8px}li:hover{background:var(--card)}li>a,li>button:not(.remove){display:flex;min-width:0;flex:1;flex-direction:column;gap:1px;padding:7px 9px;border:0;color:inherit;background:transparent;text-align:left;text-decoration:none}li strong{overflow:hidden;color:var(--ink);font-size:.78rem;text-overflow:ellipsis;white-space:nowrap}li small{color:var(--muted);font-size:.64rem}.remove{padding:0 9px;border:0;color:var(--muted-2);background:none;font-size:1rem}.other{display:block;margin-top:8px;padding:8px;border-top:1px solid var(--border);color:var(--accent);font-size:.74rem;font-weight:650;text-align:center;text-decoration:none}@media(max-width:900px){.txt,.who{display:none}}
</style>
