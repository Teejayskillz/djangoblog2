<?php
/**
 * Plugin bootstrap: wires the modules to WordPress hooks.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Wires every module to the WordPress hook system.
 */
final class NZD_LG_Plugin {

	/**
	 * Boot the plugin (called on plugins_loaded).
	 *
	 * @return void
	 */
	public static function init() {
		NZD_LG_Options::maybe_upgrade();

		NZD_LG_Entry::init();
		NZD_LG_Frontend::init();

		if ( is_admin() ) {
			NZD_LG_Admin::init();
		}

		add_action( NZD_LG_CRON_HOOK, array( 'NZD_LG_Sessions', 'cleanup' ) );

		add_action( 'init', array( __CLASS__, 'load_textdomain' ) );
	}

	/**
	 * Debug logger. Only writes when WP_DEBUG is enabled, and never logs
	 * tokens, keys or destination URLs (callers pass event codes only).
	 *
	 * @param string $message Message.
	 * @return void
	 */
	public static function log( $message ) {
		if ( ! defined( 'WP_DEBUG' ) || ! WP_DEBUG ) {
			return;
		}
		$safe = preg_replace( '/[^\x20-\x7E]/', '', (string) $message );
		error_log( '[nzdworld-link-generator] ' . $safe ); // phpcs:ignore WordPress.PHP.DevelopmentFunctions
	}

	/**
	 * Load translations when available.
	 *
	 * @return void
	 */
	public static function load_textdomain() {
		load_plugin_textdomain(
			'nzdworld-link-generator',
			false,
			dirname( plugin_basename( NZD_LG_FILE ) ) . '/languages'
		);
	}
}
