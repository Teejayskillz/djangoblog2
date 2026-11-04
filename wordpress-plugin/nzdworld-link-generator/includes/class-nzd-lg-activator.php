<?php
/**
 * Activation / deactivation routines.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Install and clean up plugin state.
 */
final class NZD_LG_Activator {

	/**
	 * Create options, the sessions table and the cleanup schedule.
	 *
	 * @return void
	 */
	public static function activate() {
		if ( ! get_option( NZD_LG_OPTION ) ) {
			NZD_LG_Options::update( NZD_LG_Options::defaults() );
		}

		NZD_LG_Sessions::install();

		if ( ! wp_next_scheduled( NZD_LG_CRON_HOOK ) ) {
			wp_schedule_event( time() + HOUR_IN_SECONDS, 'hourly', NZD_LG_CRON_HOOK );
		}

		update_option( 'nzd_lg_version', NZD_LG_VERSION, false );

		if ( ! NZD_LG_Crypto::available() ) {
			set_transient(
				'nzd_lg_activation_warning',
				'The PHP OpenSSL extension is not available, so secure download sessions cannot work. Ask your host to enable it.',
				300
			);
		}

		// The entry route is a rewrite rule; flush so /nzdworld-link/{token}/
		// works immediately after activation.
		if ( function_exists( 'flush_rewrite_rules' ) ) {
			flush_rewrite_rules( false );
		}
	}

	/**
	 * Remove the schedule and rewrite rules on deactivation.
	 *
	 * Stored data is kept so re-activating does not lose the configuration; the
	 * uninstall.php file removes everything permanently.
	 *
	 * @return void
	 */
	public static function deactivate() {
		wp_clear_scheduled_hook( NZD_LG_CRON_HOOK );
		if ( function_exists( 'flush_rewrite_rules' ) ) {
			flush_rewrite_rules( false );
		}
	}
}
