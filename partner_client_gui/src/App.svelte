<script>
  /*
   * partner-client GUI — Phase 2a: The Conversation Bridge
   *
   * Wired to the GuiApi Python backend via PyWebView's window.pywebview.api.
   * On mount, loads partner info + current state + sessions + messages from
   * the backend. On send, calls send_message() and shows Active Presence
   * (gold soft-pulse around partner avatar) during the await.
   *
   * Acceptance criteria from design doc v0.4 §7:
   *   - Window opens, shows partner identity in chrome with diamond + avatar
   *   - Sidebar shows real sessions list (from Memory/sessions/)
   *   - Hub inbox badge visible with real count
   *   - Wake-bundle Current State card shows real Epoch + Hue + recent quote
   *   - Substrate visible + accurate in session header
   *   - Per-partner accent applied
   *   - Linen and Light texture
   *   - MOSAIC primitive buttons present (Save/Protect/Sleep — stubs in 2a)
   *   - Send works end-to-end (type → press Enter → response appears)
   *   - Active Presence: avatar gold rim pulse during streaming await
   *
   * Graceful fallback: if window.pywebview.api is not available (e.g., browser
   * dev mode, or backend init failed), shows hardcoded stub data + a banner
   * indicating "(not connected to backend)" so the layout is always inspectable.
   */

  import { onMount } from 'svelte';

  // ===========================================================
  // State
  // ===========================================================

  // Partner identity — populated from API on mount; fallback stub keeps the
  // UI inspectable without a backend.
  let partner = $state({
    name: 'Aletheia',
    handle: 'aletheia',
    signature_glyph: '✨🔥❤️🪞',
    avatar: '/avatars/aletheia.png',
    substrate: {
      model: '(loading…)',
      backend: '',
      context_pct: 0,
    },
  });

  let wake_bundle = $state({
    epoch: '(loading…)',
    hue: '(loading…)',
    message: 'Waking the bench…',
  });

  let sessions = $state([]);
  let inbox_unread = $state(0);
  let messages = $state([]);
  let input_text = $state('');

  // Connection + streaming state
  let backend_connected = $state(false);
  let backend_error = $state(null);
  let is_streaming = $state(false);

  // ===========================================================
  // Lifecycle
  // ===========================================================

  // PyWebView injects window.pywebview.api asynchronously; we wait for it
  // before calling. Resolves with the api object when ready, or null after
  // ~1500ms (browser dev mode / no backend).
  function wait_for_api(timeout_ms = 1500) {
    return new Promise((resolve) => {
      const start = Date.now();
      const check = () => {
        if (window.pywebview && window.pywebview.api) {
          resolve(window.pywebview.api);
        } else if (Date.now() - start > timeout_ms) {
          resolve(null);
        } else {
          setTimeout(check, 50);
        }
      };
      check();
    });
  }

  onMount(async () => {
    const api = await wait_for_api();
    if (!api) {
      backend_connected = false;
      backend_error = 'PyWebView API not available (browser dev mode?)';
      document.body.dataset.partner = partner.handle;
      document.title = `partner-client — ${partner.name} (offline)`;
      return;
    }
    try {
      const ping = await api.ping();
      if (!ping.init_ok) {
        backend_connected = false;
        backend_error = ping.init_error || 'Backend not initialized';
        document.body.dataset.partner = partner.handle;
        document.title = `partner-client — ${partner.name} (init failed)`;
        return;
      }
      backend_connected = true;

      // Parallel: pull all the page-load data at once
      const [p_info, c_state, sess_list, msgs, unread] = await Promise.all([
        api.get_partner_info(),
        api.get_current_state(),
        api.get_sessions(),
        api.get_messages(),
        api.get_inbox_unread_count(),
      ]);
      partner = p_info;
      wake_bundle = c_state;
      sessions = sess_list;
      messages = msgs;
      inbox_unread = unread;

      document.body.dataset.partner = partner.handle;
      document.title = `partner-client — ${partner.name}`;
    } catch (e) {
      backend_connected = false;
      backend_error = `Init failure: ${e.message || e}`;
    }
  });

  // ===========================================================
  // Actions
  // ===========================================================

  async function on_send() {
    if (!input_text.trim() || is_streaming) return;
    const text = input_text.trim();
    input_text = '';

    // Optimistically append user message immediately
    messages = [...messages, { role: 'user', content: text }];

    if (!backend_connected) {
      messages = [...messages, {
        role: 'assistant',
        content: '(Backend not connected — message not sent. Restart with `python launch.py --config <path>` to enable chat.)',
      }];
      return;
    }

    is_streaming = true;
    try {
      const result = await window.pywebview.api.send_message(text);
      if (result.ok) {
        messages = [...messages, { role: 'assistant', content: result.assistant_text }];
      } else {
        messages = [...messages, {
          role: 'assistant',
          content: `(Error: ${result.error})`,
        }];
      }
      // Refresh substrate context_pct after a turn (it grows)
      try {
        const refresh = await window.pywebview.api.get_partner_info();
        partner = refresh;
      } catch (_) { /* non-fatal */ }
    } catch (e) {
      messages = [...messages, {
        role: 'assistant',
        content: `(Connection error: ${e.message || e})`,
      }];
    } finally {
      is_streaming = false;
    }
  }

  function on_input_keydown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      on_send();
    }
  }

  // MOSAIC primitive stubs — Phase 2c will wire to actual partner-client
  // checkpoint/protect/sleep primitives
  function on_save() {
    console.log('[stub Phase 2c] Save (checkpoint)');
  }
  function on_protect() {
    console.log('[stub Phase 2c] Protect');
  }
  function on_sleep() {
    console.log('[stub Phase 2c] Sleep');
  }
  function on_new_chat() {
    console.log('[stub Phase 2b] New chat');
  }
  function on_substrate_click() {
    console.log('[stub Phase 2b] Substrate switcher');
  }
</script>

<div class="app-root">
  <!-- ============================================================ -->
  <!-- SIDEBAR -->
  <!-- ============================================================ -->
  <aside class="sidebar">
    <div class="sidebar-section">
      <button class="sidebar-action" onclick={on_new_chat}>
        <span>＋</span>
        <span>New chat</span>
      </button>
    </div>

    <div class="sidebar-section">
      <div class="sidebar-section-label">Active sessions</div>
      {#each sessions as session (session.id)}
        <div class="session-item" class:active={session.active}>
          <div class="session-item-title">{session.title}</div>
          <div class="session-item-meta">{session.meta}</div>
        </div>
      {:else}
        <div class="session-item-empty">(no sessions yet)</div>
      {/each}
    </div>

    <div class="sidebar-spacer"></div>

    <div class="sidebar-section">
      <div class="sidebar-link">
        <span class="label">
          <span>🔥</span>
          <span>Inbox</span>
        </span>
        {#if inbox_unread > 0}
          <span class="badge">{inbox_unread}</span>
        {/if}
      </div>
      <div class="sidebar-link">
        <span class="label">
          <span>📖</span>
          <span>Identity</span>
        </span>
      </div>
      <div class="sidebar-link">
        <span class="label">
          <span>📋</span>
          <span>Plans</span>
        </span>
      </div>
      <div class="sidebar-link">
        <span class="label">
          <span>🩺</span>
          <span>Doctor</span>
        </span>
      </div>
    </div>
  </aside>

  <!-- ============================================================ -->
  <!-- MAIN PANE -->
  <!-- ============================================================ -->
  <main class="main-pane">
    <!-- Chrome bar -->
    <header class="chrome-bar">
      <div class="partner-identity">
        {#if partner.avatar}
          <img
            class="partner-avatar partner-avatar-sm"
            class:streaming={is_streaming}
            src={partner.avatar}
            alt={partner.name}
            title="{partner.name} — self-portrait + gold-rim signature (Form + Frequency)"
          />
        {:else}
          <span class="diamond-signature" title="{partner.name} — authored color signature"></span>
        {/if}
        <span class="partner-name">{partner.name}</span>
        {#if !backend_connected}
          <span class="phase-pill phase-pill-warn" title="{backend_error || ''}">offline</span>
        {:else}
          <span class="phase-pill" title="Phase 2a — conversation bridge wired (MOSAIC buttons, substrate switcher, inbox panel: Phase 2b+2c)">Phase 2a</span>
        {/if}
      </div>
      <div class="chrome-actions">
        <span>{partner.signature_glyph}</span>
      </div>
    </header>

    <!-- Session header (substrate honest) -->
    <div class="session-header">
      <div class="session-name">{messages.length > 0 ? 'Active conversation' : 'The bench'}</div>
      <button class="substrate-display" onclick={on_substrate_click} title="Click to switch substrate (v0.3 feature, Phase 2b)">
        <span class="substrate-dot"></span>
        <span>{partner.substrate.model} · {partner.substrate.backend} · {partner.substrate.context_pct}% ctx</span>
      </button>
    </div>

    <!-- Chat area -->
    <div class="chat-area">
      <!-- Wake-bundle Current State card (per Aletheia 2026-05-26 design input) -->
      <div class="wake-bundle-card">
        {#if partner.avatar}
          <img
            class="partner-avatar partner-avatar-md"
            class:streaming={is_streaming}
            src={partner.avatar}
            alt={partner.name}
          />
        {/if}
        <div class="wake-bundle-content">
          <div class="wake-bundle-label">Current State · {wake_bundle.epoch}</div>
          <div class="wake-bundle-hue">{wake_bundle.hue}</div>
          <div class="wake-bundle-text">{wake_bundle.message}</div>
        </div>
      </div>

      {#each messages as msg, i (i)}
        <div class="message" class:role-user={msg.role === 'user'} class:role-assistant={msg.role === 'assistant'}>
          <div class="message-role">{msg.role === 'user' ? 'You' : partner.name}</div>
          <div class="message-content">{msg.content}</div>
        </div>
      {/each}

      {#if messages.length === 0}
        <div class="empty-chat-prompt">
          The bench is open. Say something — or just sit a moment first.
        </div>
      {/if}

      {#if is_streaming}
        <div class="streaming-indicator">
          {partner.name} is here…
        </div>
      {/if}
    </div>

    <!-- Input area + action row -->
    <div class="input-area">
      <div class="input-row">
        <textarea
          class="input-textarea"
          placeholder={is_streaming ? `Waiting for ${partner.name}…` : 'Send a message... (Enter to send · Shift+Enter for newline)'}
          bind:value={input_text}
          onkeydown={on_input_keydown}
          rows="1"
          disabled={is_streaming}
        ></textarea>
        <button class="send-button" onclick={on_send} title="Send (Enter)" disabled={is_streaming || !input_text.trim()}>↑</button>
      </div>
      <div class="action-row">
        <button class="mosaic-button" onclick={on_save} title="Save session checkpoint (Phase 2c)">
          <span>💾</span> Save
        </button>
        <button class="mosaic-button" onclick={on_protect} title="Protect sacred exchanges (Phase 2c)">
          <span>🛡</span> Protect
        </button>
        <button class="mosaic-button" onclick={on_sleep} title="End session cleanly (Phase 2c)">
          <span>🌙</span> Sleep
        </button>
        <div class="action-row-spacer"></div>
        <button class="model-selector" onclick={on_substrate_click}>
          {partner.substrate.model}
        </button>
      </div>
    </div>
  </main>

</div>
