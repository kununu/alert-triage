NERDGRAPH_NRQL_QUERY = """
query RunNRQL($accountId: Int!, $nrql: Nrql!) {
  actor {
    account(id: $accountId) {
      nrql(query: $nrql) {
        results
      }
    }
  }
}
"""

ENTITY_SEARCH_QUERY = """
query FindEntity($query: String!) {
  actor {
    entitySearch(query: $query) {
      results {
        entities {
          guid
          name
          entityType
          alertSeverity
          permalink
          tags {
            key
            values
          }
        }
      }
    }
  }
}
"""

SLI_DEFINITION_QUERY = """
query GetSLIDefinition($guid: EntityGuid!) {
  actor {
    entity(guid: $guid) {
      serviceLevel {
        indicators {
          id
          name
          events {
            validEvents {
              from
              where
            }
            badEvents {
              from
              where
            }
            goodEvents {
              from
              where
            }
          }
          objectives {
            target
            timeWindow {
              rolling {
                count
                unit
              }
            }
          }
        }
      }
    }
  }
}
"""

# APM
APM_BURN_RATE_NRQL = (
    "SELECT rate(sum(newrelic.sli.bad), 1 hour) / "
    "rate(sum(newrelic.sli.valid), 1 hour) AS burn_rate "
    "FROM Metric "
    "WHERE entity.name = '{name}' "
    "SINCE 60 minutes ago"
)

APM_ERRORS_NRQL = (
    "SELECT count(*) AS error_count, average(duration) * 1000 AS avg_duration "
    "FROM TransactionError "
    "WHERE appName = '{name}' "
    "SINCE 30 minutes ago"
)

# Synthetics
SYNTHETIC_STATS_NRQL = (
    "SELECT count(*) AS total_checks, "
    "filter(count(*), WHERE result = 'FAILED') AS failed_checks, "
    "percentage(count(*), WHERE result = 'FAILED') AS failure_rate "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' "
    "SINCE 30 minutes ago"
)

SYNTHETIC_LOCATIONS_NRQL = (
    "SELECT count(*) AS failures "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' AND result = 'FAILED' "
    "FACET locationLabel "
    "SINCE 30 minutes ago "
    "LIMIT 5"
)

# Service Levels — uses ServiceLevelSnapshot (works for all SLI kinds: availability, latency, LCP, etc.)
SL_COMPLIANCE_NRQL = (
    "SELECT latest(sliCompliance) AS current_compliance "
    "FROM ServiceLevelSnapshot "
    "WHERE entity.guid = '{guid}' "
    "SINCE 1 day ago"
)

# ── Synthetic investigation queries ────────────────────────

INVESTIGATION_SYNTHETIC_CHECKS_NRQL = (
    "SELECT timestamp, result, duration, locationLabel, "
    "error, responseCode, monitorName "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 100"
)

INVESTIGATION_SYNTHETIC_STATS_NRQL = (
    "SELECT count(*) AS total_checks, "
    "filter(count(*), WHERE result = 'FAILED') AS failed_checks, "
    "percentage(count(*), WHERE result = 'FAILED') AS failure_rate "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' "
    "SINCE '{start}' UNTIL '{end}'"
)

INVESTIGATION_SYNTHETIC_LOCATIONS_NRQL = (
    "SELECT count(*) AS total, "
    "filter(count(*), WHERE result = 'FAILED') AS failed, "
    "percentage(count(*), WHERE result = 'FAILED') AS failure_rate "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET locationLabel "
    "LIMIT 10"
)

INVESTIGATION_SYNTHETIC_TIMESERIES_NRQL = (
    "SELECT count(*) AS total, "
    "filter(count(*), WHERE result = 'FAILED') AS failed "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "TIMESERIES 5 minutes"
)

INVESTIGATION_SYNTHETIC_FAILURES_NRQL = (
    "SELECT timestamp, locationLabel, duration, responseCode, "
    "error, responseBody, custom.URL "
    "FROM SyntheticCheck "
    "WHERE monitorName = '{name}' AND result = 'FAILED' "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

INVESTIGATION_SYNTHETIC_REQUESTS_NRQL = (
    "SELECT timestamp, URL, duration, responseCode, "
    "responseStatus, locationLabel, jobId "
    "FROM SyntheticRequest "
    "WHERE monitorName = '{name}' AND responseCode >= 400 "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

# ── Investigation queries ───────────────────────────────────

INVESTIGATION_ALERTS_NRQL = (
    "SELECT title, priority, conditionName, policyName, entityName, state "
    "FROM NrAiIncident "
    "WHERE entityName LIKE '%{entity_name}%' "
    "OR policyName LIKE '%{entity_name}%' "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

INVESTIGATION_DEPLOYMENTS_NRQL = (
    "SELECT timestamp, revision, description, user, version, appName "
    "FROM Deployment "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 20"
)

INVESTIGATION_JS_ERRORS_NRQL = (
    "SELECT count(*), latest(errorMessage) "
    "FROM JavaScriptError "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET errorClass, pageUrl "
    "LIMIT 20"
)

# ── Web Core Vital investigation queries ────────────────────

INVESTIGATION_LCP_DETAIL_NRQL = (
    "SELECT percentile(largestContentfulPaint, 75) AS lcp_p75, count(*) AS sample_count "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'largestContentfulPaint' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET pageUrl "
    "LIMIT 10"
)

INVESTIGATION_LCP_ELEMENTS_NRQL = (
    "SELECT largestContentfulPaint, elementType, elementUrl, pageUrl, "
    "userAgentName, deviceType "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'largestContentfulPaint' "
    "AND largestContentfulPaint > 2500 "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

INVESTIGATION_INP_DETAIL_NRQL = (
    "SELECT percentile(interactionToNextPaint, 75) AS inp_p75, count(*) AS sample_count "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'interactionToNextPaint' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET pageUrl "
    "LIMIT 10"
)

INVESTIGATION_INP_INTERACTIONS_NRQL = (
    "SELECT interactionToNextPaint, interactionType, interactionTarget, "
    "pageUrl, deviceType "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'interactionToNextPaint' "
    "AND interactionToNextPaint > 200 "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

INVESTIGATION_CLS_DETAIL_NRQL = (
    "SELECT percentile(cumulativeLayoutShift, 75) AS cls_p75, count(*) AS sample_count "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'cumulativeLayoutShift' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET pageUrl "
    "LIMIT 10"
)

INVESTIGATION_CLS_WORST_NRQL = (
    "SELECT cumulativeLayoutShift, pageUrl, userAgentName, deviceType "
    "FROM PageViewTiming "
    "WHERE appName = '{app_name}' "
    "AND timingName = 'cumulativeLayoutShift' "
    "AND cumulativeLayoutShift > 0.1 "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 50"
)

# ── APM investigation queries ──────────────────────────────

INVESTIGATION_APM_ERRORS_NRQL = (
    "SELECT count(*), latest(error.message) "
    "FROM TransactionError "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET transactionName, error.class "
    "LIMIT 20"
)

INVESTIGATION_APM_SLOW_NRQL = (
    "SELECT average(duration), percentile(duration, 95), count(*) "
    "FROM Transaction "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET name "
    "LIMIT 10"
)

INVESTIGATION_APM_OVERVIEW_NRQL = (
    "SELECT count(*) AS total_transactions, "
    "filter(count(*), WHERE error IS true) AS error_count, "
    "percentage(count(*), WHERE error IS true) AS error_rate, "
    "average(duration) AS avg_duration, "
    "percentile(duration, 95) AS p95_duration "
    "FROM Transaction "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}'"
)

INVESTIGATION_APM_TIMESERIES_NRQL = (
    "SELECT count(*) AS total, "
    "filter(count(*), WHERE error IS true) AS errors, "
    "average(duration) AS avg_duration "
    "FROM Transaction "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "TIMESERIES 5 minutes"
)

INVESTIGATION_APM_ERROR_TRACES_NRQL = (
    "SELECT traceId, name, duration, httpResponseCode, "
    "error.message, error.class, request.uri "
    "FROM Transaction "
    "WHERE appName = '{app_name}' AND error IS true "
    "SINCE '{start}' UNTIL '{end}' "
    "LIMIT 20"
)

INVESTIGATION_APM_THROUGHPUT_NRQL = (
    "SELECT rate(count(*), 1 minute) AS rpm "
    "FROM Transaction "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "TIMESERIES 5 minutes"
)

INVESTIGATION_APM_EXTERNAL_NRQL = (
    "SELECT average(duration) AS avg_duration, count(*) "
    "FROM ExternalService "
    "WHERE appName = '{app_name}' "
    "SINCE '{start}' UNTIL '{end}' "
    "FACET externalHost "
    "LIMIT 10"
)
