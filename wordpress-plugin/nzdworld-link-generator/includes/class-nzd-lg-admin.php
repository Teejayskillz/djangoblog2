<?php
/**
 * WordPress admin screen and settings handling.
 *
 * Everything on this screen requires the `manage_options` capability, every
 * write goes through a nonce-checked POST handler, and the API key is masked in
 * the form (leaving the field blank keeps the stored key).
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Admin menu, settings, connection test and statistics.
 */
final class NZD_LG_Admin {

	/**
	 * Capability required for every admin action.
	 */
	const CAPABILITY = 'manage_options';

	/**
	 * Admin page slug.
	 */
	const PAGE_SLUG = 'nzdworld-link-generator';

	/**
	 * Register hooks.
	 *
	 * @return void
	 */
	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'register_menu' ) );
		add_action( 'admin_post_nzd_lg_save', array( __CLASS__, 'handle_save' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( NZD_LG_FILE ), array( __CLASS__, 'action_links' ) );
	}

	/**
	 * Add the admin menu entry.
	 *
	 * @return void
	 */
	public static function register_menu() {
		add_menu_page(
			__( 'NZDWorld Link Generator', 'nzdworld-link-generator' ),
			__( 'NZDWorld Link Generator', 'nzdworld-link-generator' ),
			self::CAPABILITY,
			self::PAGE_SLUG,
			array( __CLASS__, 'render' ),
			'dashicons-download',
			58
		);
	}

	/**
	 * Add a "Settings" link on the Plugins screen.
	 *
	 * @param array $links Existing links.
	 * @return array
	 */
	public static function action_links( $links ) {
		$url = add_query_arg( 'page', self::PAGE_SLUG, admin_url( 'admin.php' ) );
		array_unshift(
			$links,
			'<a href="' . esc_url( $url ) . '">' . esc_html__( 'Settings', 'nzdworld-link-generator' ) . '</a>'
		);
		return $links;
	}

	/**
	 * Render the settings screen.
	 *
	 * @return void
	 */
	public static function render() {
		if ( ! current_user_can( self::CAPABILITY ) ) {
			wp_die( esc_html__( 'You do not have permission to access this page.', 'nzdworld-link-generator' ), 403 );
		}

		$settings       = NZD_LG_Options::get();
		$stats          = NZD_LG_Options::stats();
		$session_counts = NZD_LG_Sessions::counts();
		$notice         = self::take_notice();
		$categories     = get_categories(
			array(
				'hide_empty' => false,
				'number'     => 300,
			)
		);
		$entry_template = NZD_LG_Api_Client::entry_template();
		$crypto_ok      = NZD_LG_Crypto::available();
		$https_ok       = NZD_LG_Api_Client::is_secure_url( home_url( '/' ) );

		$template = NZD_LG_DIR . 'templates/admin-settings.php';
		if ( is_readable( $template ) ) {
			include $template;
		}
	}

	/**
	 * Save handler (admin-post.php?action=nzd_lg_save).
	 *
	 * @return void
	 */
	public static function handle_save() {
		if ( ! current_user_can( self::CAPABILITY ) ) {
			wp_die( esc_html__( 'You do not have permission to do that.', 'nzdworld-link-generator' ), 403 );
		}

		check_admin_referer( 'nzd_lg_save' );

		$existing = NZD_LG_Options::get();
		$input    = isset( $_POST['nzd_lg'] ) && is_array( $_POST['nzd_lg'] ) ? wp_unslash( $_POST['nzd_lg'] ) : array();
		$new      = $existing;
		$problems = array();

		$new['enabled'] = empty( $input['enabled'] ) ? 0 : 1;

		// --- NZDWorld URL --------------------------------------------------
		$raw_nzd = isset( $input['nzd_site_url'] ) ? (string) $input['nzd_site_url'] : '';
		$nzd_url = NZD_LG_Options::clean_url( $raw_nzd );
		if ( '' !== trim( $raw_nzd ) && '' === $nzd_url ) {
			$problems[] = 'The NZDWorld website URL must be an absolute HTTPS URL, e.g. https://nzdworld.com';
		}
		$new['nzd_site_url'] = $nzd_url;

		// --- Blog page link ------------------------------------------------
		$raw_blog = isset( $input['blog_page_url'] ) ? (string) $input['blog_page_url'] : '';
		$blog_url = NZD_LG_Options::clean_same_site_url( $raw_blog );
		if ( '' !== trim( $raw_blog ) && '' === $blog_url ) {
			$problems[] = 'The blog page link must be an absolute URL on this website (same host), e.g. https://this-site.com/jobs/';
		}
		$new['blog_page_url'] = $blog_url;

		// --- API key -------------------------------------------------------
		$submitted_key = isset( $input['api_key'] ) ? trim( sanitize_text_field( (string) $input['api_key'] ) ) : '';
		$clear_key     = isset( $input['clear_api_key'] ) && '1' === (string) $input['clear_api_key'];

		if ( $clear_key ) {
			$new['api_key']       = '';
			$new['api_key_last4'] = '';
		} elseif ( '' !== $submitted_key ) {
			if ( ! preg_match( '/^nzd_live_[A-Za-z0-9_-]{20,}$/', $submitted_key ) ) {
				$problems[] = 'That does not look like an NZDWorld API key. It should start with "nzd_live_".';
			} else {
				$new['api_key']       = $submitted_key;
				$new['api_key_last4'] = substr( $submitted_key, -4 );
			}
		}

		// --- Countdown -----------------------------------------------------
		$server_min = max( 1, (int) $existing['min_countdown_seconds'] );
		$server_max = max( $server_min, (int) $existing['max_countdown_seconds'] );
		$min        = max( 5, $server_min );
		$max        = max( $min, min( 120, $server_max ) );
		$new['countdown_seconds'] = NZD_LG_Options::clamp_int(
			isset( $input['countdown_seconds'] ) ? $input['countdown_seconds'] : 20,
			$min,
			$max,
			20
		);

		// --- Article routing ----------------------------------------------
		$new['random_article'] = empty( $input['random_article'] ) ? 0 : 1;
		$new['use_all_posts']  = empty( $input['use_all_posts'] ) ? 0 : 1;

		$categories = isset( $input['allowed_categories'] ) ? (array) $input['allowed_categories'] : array();
		$categories = array_values( array_filter( array_map( 'absint', $categories ) ) );
		$new['allowed_categories'] = $categories;

		if ( 0 === $new['random_article'] && '' === $new['blog_page_url'] ) {
			$problems[] = 'Random article routing is off, so a blog page link is required as the popup destination.';
		}
		if ( 1 === $new['random_article'] && 0 === $new['use_all_posts'] && empty( $categories ) ) {
			$problems[] = 'Choose at least one category, or enable "use all published posts".';
		}

		// --- Popup appearance ---------------------------------------------
		$heading = isset( $input['popup_heading'] ) ? sanitize_text_field( (string) $input['popup_heading'] ) : '';
		$heading = '' !== $heading ? NZD_LG_Options::trim_text( $heading, 120 ) : 'Generating your download link';
		$new['popup_heading'] = $heading;

		$description = isset( $input['popup_description'] ) ? sanitize_textarea_field( (string) $input['popup_description'] ) : '';
		$description = '' !== trim( $description ) ? NZD_LG_Options::trim_text( $description, 400 ) : 'Please wait while we prepare your link.';
		$new['popup_description'] = $description;

		$button = isset( $input['popup_button_text'] ) ? sanitize_text_field( (string) $input['popup_button_text'] ) : '';
		$button = '' !== $button ? NZD_LG_Options::trim_text( $button, 40 ) : 'Click to Proceed';
		$new['popup_button_text'] = $button;

		$logo = isset( $input['popup_logo_url'] ) ? NZD_LG_Options::clean_url( (string) $input['popup_logo_url'] ) : '';
		if ( '' !== trim( (string) ( isset( $input['popup_logo_url'] ) ? $input['popup_logo_url'] : '' ) ) && '' === $logo ) {
			$problems[] = 'The popup logo must be an absolute HTTPS image URL.';
		}
		$new['popup_logo_url'] = $logo;

		// --- Session lifetime ---------------------------------------------
		$max_lifetime = max( 120, min( 3600, (int) $existing['max_session_seconds'] ) );
		$new['session_ttl'] = NZD_LG_Options::clamp_int(
			isset( $input['session_ttl'] ) ? $input['session_ttl'] : 900,
			120,
			$max_lifetime,
			900
		);

		$new = NZD_LG_Options::update( $new );

		$messages = array();
		$type     = empty( $problems ) ? 'success' : 'error';

		if ( ! empty( $problems ) ) {
			$messages = $problems;
		} else {
			$messages[] = 'Settings saved.';
		}

		// Optional connection test in the same submission.
		$run_test = isset( $_POST['nzd_lg_test'] );
		if ( $run_test ) {
			$test = self::run_connection_test();
			if ( 'success' !== $test['type'] ) {
				$type = $test['type'];
			}
			$messages = array_merge( $messages, $test['messages'] );
		}

		self::store_notice( $type, $messages );

		wp_safe_redirect( add_query_arg( 'page', self::PAGE_SLUG, admin_url( 'admin.php' ) ) );
		exit;
	}

	/**
	 * Run a connection test against NZDWorld and store the outcome.
	 *
	 * @return array {type, messages}
	 */
	public static function run_connection_test() {
		$settings = NZD_LG_Options::get();

		if ( '' === $settings['nzd_site_url'] || '' === $settings['api_key'] ) {
			NZD_LG_Options::set_connection_state( 'unknown', 'Missing NZDWorld URL or API key.' );
			return array(
				'type'     => 'error',
				'messages' => array( 'Add the NZDWorld website URL and API key before testing the connection.' ),
			);
		}

		$client = NZD_LG_Api_Client::from_settings();
		$result = $client->connection_test();

		if ( ! empty( $result['ok'] ) ) {
			$data = isset( $result['data'] ) && is_array( $result['data'] ) ? $result['data'] : array();
			NZD_LG_Options::remember_limits( $data );

			$max_lifetime = isset( $data['session_ttl_seconds'] ) ? (int) $data['session_ttl_seconds'] : 0;
			$enabled      = ! empty( $data['integration_enabled'] );
			$message      = isset( $data['message'] ) ? sanitize_text_field( (string) $data['message'] ) : '';

			if ( $enabled ) {
				NZD_LG_Options::set_connection_state( 'connected', $message, $max_lifetime );
				return array(
					'type'     => 'success',
					'messages' => array(
						'Connected to NZDWorld successfully. The generator is enabled there and this site is registered.',
						'NZDWorld allows a maximum session lifetime of ' . max( 0, $max_lifetime ) . ' seconds.',
					),
				);
			}

			NZD_LG_Options::set_connection_state( 'disabled', $message, $max_lifetime );
			return array(
				'type'     => 'warning',
				'messages' => array(
					'Connected to NZDWorld, but the External Link Generator is disabled there. Enable it in the NZDWorld Control Center to start the handoff.',
				),
			);
		}

		$code      = isset( $result['code'] ) ? (string) $result['code'] : 'invalid_response';
		$technical = isset( $result['technical'] ) && '' !== $result['technical'] ? ' (' . $result['technical'] . ')' : '';
		NZD_LG_Options::set_connection_state( 'error', $code );

		return array(
			'type'     => 'error',
			'messages' => array(
				'Connection test failed: ' . NZD_LG_Entry::visitor_message( $result ) . $technical,
				'Error code: ' . $code,
			),
		);
	}

	/**
	 * Store a one-time admin notice for the current user.
	 *
	 * @param string $type     success|error|warning.
	 * @param array  $messages Lines of text.
	 * @return void
	 */
	private static function store_notice( $type, $messages ) {
		set_transient(
			'nzd_lg_notice_' . get_current_user_id(),
			array(
				'type'     => in_array( $type, array( 'success', 'error', 'warning' ), true ) ? $type : 'success',
				'messages' => array_values( array_map( 'strval', (array) $messages ) ),
			),
			5 * MINUTE_IN_SECONDS
		);
	}

	/**
	 * Read and remove the one-time notice.
	 *
	 * @return array|null
	 */
	private static function take_notice() {
		$key    = 'nzd_lg_notice_' . get_current_user_id();
		$notice = get_transient( $key );
		if ( false === $notice ) {
			return null;
		}
		delete_transient( $key );
		return is_array( $notice ) ? $notice : null;
	}
}
