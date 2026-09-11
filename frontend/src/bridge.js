// Shared wording for the index bridge, so every panel describes it the same way.
//
// The bridge carries a stale construction index observation forward to the tender
// quarter using the published movement of a price index. A PRODUCER price index
// is used first, because it measures what manufacturers and utilities charge for
// the commodity baskets a construction rate is made of. A CONSUMER price index is
// the fallback, reached only when no producer series spans the window - it
// measures what households pay, which is a weaker proxy.
//
// Either way the bridged value is DERIVED to show the trend to date: it is not a
// published observation for the tender quarter.

export function bridgeKindLong(kind) {
  return kind === 'PPI' ? 'producer price index' : 'consumer price index'
}

export function bridgeKindShort(kind) {
  return kind === 'PPI' ? 'PPI' : 'CPI'
}

/** Which index the bridge used. Older payloads only carried the cpi_* names. */
export function bridgeSeries(bridge) {
  return (bridge && (bridge.series_name || bridge.cpi_series_name)) || ''
}

export function bridgeKind(bridge) {
  return (bridge && bridge.kind) || 'CPI'
}

export function bridgeFactor(bridge) {
  return bridge ? bridge.cpi_bridge_factor : 1
}

export function bridgeFromMonths(bridge) {
  return (bridge && bridge.cpi_from_months) || []
}

export function bridgeToMonths(bridge) {
  return (bridge && bridge.cpi_to_months) || []
}

export function bridgeFromValue(bridge) {
  return bridge ? bridge.cpi_from_value : null
}

export function bridgeToValue(bridge) {
  return bridge ? bridge.cpi_to_value : null
}

/** True when the bridge fell back to consumer prices because no PPI was available. */
export function bridgeUsedFallback(bridge) {
  return !!bridge && bridge.applied && bridgeKind(bridge) !== 'PPI'
}

const REASONS = {
  index_observation_covers_requested_quarter: 'the index observation already covers the quarter being priced, so no bridge is needed',
  bridge_disabled_by_analyst: 'bridging is switched off for this run',
  no_price_observations_loaded: 'no monthly price series is loaded for this market',
  no_price_series_covers_both_quarters:
    'no loaded price series spans both the index quarter and the tender quarter (the publisher rebased the index, and the older and newer series are deliberately not chained)',
  no_price_observation_for_the_observation_quarter:
    'no price series has an observation for the index quarter',
  no_price_observation_after_the_index_observation:
    'no price series has an observation after the index quarter',
  price_value_at_the_observation_quarter_is_zero:
    'the price index value at the index quarter is zero',
  bridged_with_price_index: 'bridged',

  // Legacy reason codes, kept so an older stored run still explains itself.
  no_cpi_series_configured_for_country: 'no price series is configured for this market',
  no_cpi_observations_loaded: 'no monthly price series is loaded for this market',
  no_cpi_series_covers_both_quarters:
    'no loaded price series spans both the index quarter and the tender quarter',
  no_cpi_observation_for_the_observation_quarter:
    'no price series has an observation for the index quarter',
  no_cpi_observation_after_the_index_observation:
    'no price series has an observation after the index quarter',
  cpi_value_at_the_observation_quarter_is_zero:
    'the price index value at the index quarter is zero',
}

export function bridgeReason(reason) {
  if (!reason) return 'no index bridge was applied'
  return REASONS[reason] || 'no index bridge is available for this run'
}

/**
 * One sentence describing how the engine brought the index up to the quarter
 * being priced. Returns null when no bridge object was supplied.
 */
export function bridgeSourcedFrom(bridge) {
  if (!bridge || !bridge.applied) return null
  const kind = bridgeKindLong(bridgeKind(bridge))
  return bridgeSeries(bridge) + ' (' + kind + ')'
}
