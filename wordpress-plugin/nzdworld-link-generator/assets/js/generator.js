/*
 * NZDWorld Link Generator - popup behaviour.
 *
 * The countdown is cosmetic: the WordPress backend and NZDWorld both enforce the
 * server-side "not before" time, so editing this file cannot release a download
 * early. Refreshing the page re-reads the remaining seconds from the server, so
 * the countdown never restarts.
 */
(function () {
	'use strict';

	var config = window.NzdLgConfig;
	if (!config || !config.session) {
		return;
	}

	var CIRCUMFERENCE = 326.73;

	function el(id) {
		return document.getElementById(id);
	}

	var overlay = el('nzd-lg-overlay');
	var modal = el('nzd-lg-modal');
	var waiting = el('nzd-lg-waiting');
	var ready = el('nzd-lg-ready');
	var errorBox = el('nzd-lg-error');
	var errorText = el('nzd-lg-error-text');
	var retryBtn = el('nzd-lg-retry');
	var closeBtn = el('nzd-lg-close');
	var reopenBtn = el('nzd-lg-reopen');
	var proceedBtn = el('nzd-lg-proceed');
	var secondsEl = el('nzd-lg-seconds');
	var statusSecondsEl = el('nzd-lg-status-seconds');
	var ring = el('nzd-lg-ring');
	var bar = el('nzd-lg-bar');
	var progress = el('nzd-lg-progress');

	if (!overlay || !modal || !waiting || !ready || !errorBox) {
		return;
	}

	var strings = config.strings || {};
	var total = Math.max(1, parseInt(config.duration, 10) || 1);
	var deadline = 0;
	var timer = null;
	var busy = false;
	var retryMode = 'proceed';

	function show(node) {
		if (node) {
			node.hidden = false;
		}
	}

	function hide(node) {
		if (node) {
			node.hidden = true;
		}
	}

	function setState(state) {
		hide(waiting);
		hide(ready);
		hide(errorBox);
		if (state === 'waiting') {
			show(waiting);
		} else if (state === 'ready') {
			show(ready);
		} else if (state === 'error') {
			show(errorBox);
		}
	}

	function focusModal() {
		if (!modal || typeof modal.focus !== 'function') {
			return;
		}
		try {
			modal.focus({ preventScroll: true });
		} catch (e) {
			modal.focus();
		}
	}

	function openModal() {
		overlay.hidden = false;
		overlay.setAttribute('aria-hidden', 'false');
		document.body.classList.add('nzd-lg-open');
		hide(reopenBtn);
		focusModal();
	}

	function closeModal() {
		overlay.hidden = true;
		overlay.setAttribute('aria-hidden', 'true');
		document.body.classList.remove('nzd-lg-open');
		show(reopenBtn);
	}

	function renderProgress(left) {
		var fraction = total > 0 ? Math.min(1, Math.max(0, left / total)) : 0;
		var percent = Math.round((1 - fraction) * 100);

		if (secondsEl) {
			secondsEl.textContent = String(left);
		}
		if (ring) {
			ring.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - fraction));
		}
		if (bar) {
			bar.style.width = percent + '%';
		}
		if (progress) {
			progress.setAttribute('aria-valuenow', String(percent));
		}
		if (statusSecondsEl) {
			statusSecondsEl.textContent = left > 0
				? left + ' ' + (strings.seconds || 'seconds')
				: '';
		}
	}

	function finishCountdown() {
		renderProgress(0);
		setState('ready');
	}

	function stopTimer() {
		if (timer) {
			clearInterval(timer);
			timer = null;
		}
	}

	function tick() {
		var msLeft = deadline - Date.now();
		var left = Math.max(0, Math.ceil(msLeft / 1000));
		renderProgress(left);
		if (msLeft <= 0) {
			stopTimer();
			finishCountdown();
		}
	}

	function startCountdown(seconds) {
		seconds = Math.max(0, parseInt(seconds, 10) || 0);
		stopTimer();
		setState('waiting');

		if (seconds === 0) {
			finishCountdown();
			return;
		}

		deadline = Date.now() + (seconds * 1000);
		renderProgress(seconds);
		timer = setInterval(tick, 250);
	}

	function setProceedBusy(state) {
		if (!proceedBtn) {
			return;
		}
		proceedBtn.disabled = !!state;
		proceedBtn.setAttribute('aria-busy', state ? 'true' : 'false');
		if (state) {
			proceedBtn.textContent = 'Preparing…';
		} else {
			proceedBtn.textContent = proceedBtn.getAttribute('data-label') || proceedBtn.textContent;
		}
	}

	function rememberButtonLabel() {
		if (proceedBtn && !proceedBtn.getAttribute('data-label')) {
			proceedBtn.setAttribute('data-label', proceedBtn.textContent.trim());
		}
	}

	function handleError(payload) {
		payload = payload || {};
		var remaining = parseInt(payload.remaining, 10) || 0;

		if (payload.code === 'too_early' && remaining > 0) {
			// Server says the countdown is not over (clock skew or tampering).
			startCountdown(remaining);
			return;
		}

		retryMode = payload.code === 'bad_nonce' ? 'reload' : 'proceed';

		if (errorText) {
			errorText.textContent = payload.message || strings.network || 'Something went wrong. Please try again.';
		}
		if (retryBtn) {
			retryBtn.hidden = !payload.retryable;
			retryBtn.textContent = retryMode === 'reload'
				? 'Reload page'
				: (strings.retry || 'Try again');
		}
		setState('error');
	}

	function proceed() {
		if (busy) {
			return;
		}

		busy = true;
		rememberButtonLabel();
		setProceedBusy(true);

		var body = new URLSearchParams();
		body.append('action', config.action);
		body.append('nonce', config.nonce);
		body.append('session', config.session);

		fetch(config.ajaxUrl, {
			method: 'POST',
			credentials: 'same-origin',
			headers: {
				'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
				'X-Requested-With': 'XMLHttpRequest'
			},
			body: body.toString()
		})
			.then(function (response) {
				return response.json().catch(function () {
					return {
						success: false,
						data: {
							code: 'invalid_response',
							message: strings.network || 'Unexpected response from the server.',
							retryable: true
						}
					};
				});
			})
			.then(function (result) {
				busy = false;
				setProceedBusy(false);

				if (result && result.success && result.data && result.data.redirect) {
					var url = String(result.data.redirect);
					if (!/^https?:\/\//i.test(url)) {
						handleError({
							code: 'invalid_response',
							message: strings.network,
							retryable: true
						});
						return;
					}
					window.location.assign(url);
					return;
				}

				handleError(result && result.data ? result.data : {});
			})
			.catch(function () {
				busy = false;
				setProceedBusy(false);
				handleError({
					code: 'network_error',
					message: strings.network || 'We could not reach the server. Please try again.',
					retryable: true
				});
			});
	}

	function retry() {
		if (retryMode === 'reload') {
			window.location.reload();
			return;
		}
		hide(errorBox);
		if (timer === null) {
			setState('ready');
		}
		proceed();
	}

	if (proceedBtn) {
		proceedBtn.addEventListener('click', proceed);
	}
	if (retryBtn) {
		retryBtn.addEventListener('click', retry);
	}
	if (closeBtn) {
		closeBtn.addEventListener('click', closeModal);
	}
	if (reopenBtn) {
		reopenBtn.addEventListener('click', openModal);
	}

	overlay.addEventListener('click', function (event) {
		if (event.target === overlay) {
			closeModal();
		}
	});

	document.addEventListener('keydown', function (event) {
		if (overlay.hidden) {
			return;
		}
		if (event.key === 'Escape') {
			closeModal();
		}
		if (event.key !== 'Tab') {
			return;
		}

		var focusable = modal.querySelectorAll(
			'button:not([disabled]):not([hidden]), a[href], [tabindex]:not([tabindex="-1"])'
		);
		var visible = [];
		for (var i = 0; i < focusable.length; i++) {
			if (focusable[i].offsetParent !== null) {
				visible.push(focusable[i]);
			}
		}
		if (!visible.length) {
			return;
		}

		var first = visible[0];
		var last = visible[visible.length - 1];

		if (event.shiftKey && document.activeElement === first) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && document.activeElement === last) {
			event.preventDefault();
			first.focus();
		}
	});

	function boot() {
		rememberButtonLabel();
		openModal();
		startCountdown(parseInt(config.remaining, 10) || 0);
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', boot);
	} else {
		boot();
	}
})();
