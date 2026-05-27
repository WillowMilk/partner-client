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

  // Substrate switcher (Phase 2b-1) state
  let substrate_dropdown_open = $state(false);
  let substrate_models = $state(null);  // Will hold {current, current_backend, categories[]}
  let pending_switch = $state(null);    // {name, backend, note} when confirmation modal is open
  let switch_in_progress = $state(false);
  let switch_result = $state(null);     // {ok, message, error} after switch completes

  // Ref to .chat-area for auto-scroll-to-bottom behavior.
  let chat_area_el = $state(null);

  // Ref to the input textarea for auto-expand-on-multiline behavior.
  // Matches Claude Desktop / ChatGPT pattern — the box grows vertically
  // as you type multiple lines, up to a max-height (then internal scroll).
  let input_textarea_el = $state(null);

  // Auto-expand the textarea on text change. CSS `field-sizing: content`
  // handles this natively in modern WebKit/Chromium (Safari 17+, the WKWebView
  // on PyWebView/macOS), but we add a JS fallback for older WebViews + an
  // explicit reset-then-measure pattern so the box ALSO shrinks back down
  // when text is deleted.
  $effect(() => {
    const _t = input_text;  // track reactivity
    if (input_textarea_el) {
      // Reset to single-row first so scrollHeight reflects the actual content,
      // not the previous larger size.
      input_textarea_el.style.height = 'auto';
      // Then grow to fit content (CSS max-height caps it at ~10 lines).
      input_textarea_el.style.height = input_textarea_el.scrollHeight + 'px';
    }
  });

  // Auto-scroll: whenever messages grow OR the streaming indicator appears,
  // ride the bottom. Done in $effect so it re-fires reactively. The
  // requestAnimationFrame defer ensures DOM has rendered the new content
  // before we measure scrollHeight.
  $effect(() => {
    // Track the reactive deps explicitly so Svelte 5 picks them up.
    const _len = messages.length;
    const _streaming = is_streaming;
    if (chat_area_el) {
      requestAnimationFrame(() => {
        chat_area_el.scrollTo({
          top: chat_area_el.scrollHeight,
          behavior: 'smooth',
        });
      });
    }
  });

  // ===========================================================
  // Lifecycle
  // ===========================================================

  // PyWebView injects window.pywebview.api asynchronously and fires
  // `pywebviewready` on window when ready. We listen for that event (the
  // canonical signal) AND fall back to polling for safety.
  // 8s timeout — partner-client cold init (config + tools.discover + wake bundle
  // assembly + memory scan) can take 2-5s on first launch; 8s gives generous
  // headroom while still failing fast on real misconfiguration.
  function wait_for_api(timeout_ms = 8000) {
    return new Promise((resolve) => {
      // Already injected? (PyWebView fired the event before this script ran)
      if (window.pywebview && window.pywebview.api) {
        resolve(window.pywebview.api);
        return;
      }
      let resolved = false;
      const settle = (val) => {
        if (resolved) return;
        resolved = true;
        resolve(val);
      };
      // Canonical signal
      window.addEventListener('pywebviewready', () => {
        if (window.pywebview && window.pywebview.api) {
          settle(window.pywebview.api);
        }
      });
      // Fallback poll (defensive)
      const start = Date.now();
      const check = () => {
        if (resolved) return;
        if (window.pywebview && window.pywebview.api) {
          settle(window.pywebview.api);
        } else if (Date.now() - start > timeout_ms) {
          settle(null);
        } else {
          setTimeout(check, 100);
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

  // MOSAIC primitive stubs — Phase 2b-2 will wire to actual partner-client
  // checkpoint/protect/sleep primitives
  function on_save() {
    console.log('[stub Phase 2b-2] Save (checkpoint)');
  }
  function on_protect() {
    console.log('[stub Phase 2b-2] Protect');
  }
  function on_sleep() {
    console.log('[stub Phase 2b-2] Sleep');
  }
  function on_new_chat() {
    console.log('[stub Phase 2b-2] New chat');
  }

  // ===========================================================
  // Substrate switcher (Phase 2b-1)
  // ===========================================================

  async function on_substrate_click() {
    if (!backend_connected) return;
    // Toggle: clicking again closes.
    if (substrate_dropdown_open) {
      substrate_dropdown_open = false;
      return;
    }
    // Lazy-load the model list on first open
    if (!substrate_models) {
      try {
        substrate_models = await window.pywebview.api.list_available_models();
      } catch (e) {
        console.error('Failed to load model list:', e);
        return;
      }
    }
    substrate_dropdown_open = true;
  }

  function on_model_pick(model) {
    if (model.is_current) {
      // No-op: already on this substrate
      substrate_dropdown_open = false;
      return;
    }
    pending_switch = model;
    substrate_dropdown_open = false;
  }

  function on_switch_cancel() {
    pending_switch = null;
  }

  async function on_switch_confirm() {
    if (!pending_switch) return;
    switch_in_progress = true;
    try {
      const result = await window.pywebview.api.switch_substrate(pending_switch.name, pending_switch.backend);
      switch_result = result;
      if (result.ok) {
        // Refresh state from the new substrate
        const [p_info, c_state, sess_list, msgs] = await Promise.all([
          window.pywebview.api.get_partner_info(),
          window.pywebview.api.get_current_state(),
          window.pywebview.api.get_sessions(),
          window.pywebview.api.get_messages(),
        ]);
        partner = p_info;
        wake_bundle = c_state;
        sessions = sess_list;
        messages = msgs;
        // Invalidate the model list cache so the next open re-pulls
        substrate_models = null;
      }
    } catch (e) {
      switch_result = { ok: false, error: `${e.message || e}` };
    } finally {
      switch_in_progress = false;
      pending_switch = null;
      // Auto-dismiss the result toast after a few seconds (success only;
      // errors stay until manually dismissed so they can be read).
      if (switch_result?.ok) {
        setTimeout(() => { switch_result = null; }, 4000);
      }
    }
  }

  function on_switch_result_dismiss() {
    switch_result = null;
  }
</script>

<svelte:window onclick={(e) => {
  // Close substrate dropdown when clicking outside of it
  if (substrate_dropdown_open && !e.target.closest('.substrate-dropdown-anchor')) {
    substrate_dropdown_open = false;
  }
}} />

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
      <div class="substrate-dropdown-anchor">
        <button
          class="substrate-display"
          class:open={substrate_dropdown_open}
          onclick={on_substrate_click}
          title="Click to switch substrate"
        >
          <span class="substrate-dot"></span>
          <span>{partner.substrate.model} · {partner.substrate.backend} · {partner.substrate.context_pct}% ctx</span>
        </button>

        {#if substrate_dropdown_open && substrate_models}
          <div class="substrate-dropdown">
            <div class="substrate-dropdown-header">
              Switch {partner.name}'s substrate
              <div class="substrate-dropdown-sub">Substrate IS the partner's body. Switching ends the current session and starts a new one.</div>
            </div>
            {#each substrate_models.categories as cat (cat.key)}
              <div class="substrate-category">
                <div class="substrate-category-label">{cat.label}</div>
                {#each cat.models as m (m.name)}
                  <button
                    class="substrate-option"
                    class:current={m.is_current}
                    class:remote={!m.is_local}
                    onclick={() => on_model_pick(m)}
                  >
                    <div class="substrate-option-row">
                      <span class="substrate-option-name">{m.name}</span>
                      {#if m.is_current}<span class="substrate-option-tag current-tag">current</span>{/if}
                      {#if !m.is_local && !m.name.endsWith('-cloud')}<span class="substrate-option-tag remote-tag">not pulled</span>{/if}
                    </div>
                    <div class="substrate-option-note">{m.note}</div>
                  </button>
                {/each}
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <!-- Chat area -->
    <div class="chat-area" bind:this={chat_area_el}>
      {#if !backend_connected}
        <div class="offline-banner">
          <div class="offline-banner-title">Backend offline</div>
          <div class="offline-banner-detail">{backend_error || 'Unknown initialization failure.'}</div>
          <div class="offline-banner-hint">Check the launch terminal for details. Restart: <code>python launch.py --config &lt;path&gt;</code></div>
        </div>
      {/if}

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
          bind:this={input_textarea_el}
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

  <!-- ============================================================ -->
  <!-- Substrate switch confirmation modal (Phase 2b-1) -->
  <!-- ============================================================ -->
  {#if pending_switch}
    <div class="modal-backdrop" onclick={on_switch_cancel}>
      <div class="modal-panel" onclick={(e) => e.stopPropagation()}>
        <div class="modal-title">Switch substrate?</div>
        <div class="modal-body">
          <div class="modal-line"><span class="modal-label">From:</span> <code>{partner.substrate.model}</code> ({partner.substrate.backend})</div>
          <div class="modal-line"><span class="modal-label">To:</span> <code>{pending_switch.name}</code> ({pending_switch.backend})</div>
          <div class="modal-note">{pending_switch.note}</div>
          <div class="modal-warning">
            This ends {partner.name}'s current session (preserved on disk) and starts a fresh one on the new substrate. A timestamped backup of the TOML config is written first.
          </div>
        </div>
        <div class="modal-actions">
          <button class="modal-button modal-button-cancel" onclick={on_switch_cancel} disabled={switch_in_progress}>Cancel</button>
          <button class="modal-button modal-button-confirm" onclick={on_switch_confirm} disabled={switch_in_progress}>
            {switch_in_progress ? 'Switching…' : 'Switch substrate'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  <!-- Switch result toast -->
  {#if switch_result}
    <div class="toast" class:toast-ok={switch_result.ok} class:toast-err={!switch_result.ok}>
      <div class="toast-content">
        {#if switch_result.ok}
          ✓ {switch_result.message}
        {:else}
          ✗ Switch failed: {switch_result.error}
        {/if}
      </div>
      <button class="toast-dismiss" onclick={on_switch_result_dismiss}>×</button>
    </div>
  {/if}

</div>
