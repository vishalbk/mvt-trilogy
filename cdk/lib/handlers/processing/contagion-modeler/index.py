import json
import os
from datetime import datetime
import boto3
import logging
from decimal import Decimal
from collections import defaultdict

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')

SIGNALS_TABLE = os.environ.get('SIGNALS_TABLE')
DASHBOARD_STATE_TABLE = os.environ.get('DASHBOARD_STATE_TABLE')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME')

# Risk score weights (aligned with worldbank-poller indicators)
WEIGHTS = {
    'gni_vulnerability': 0.35,    # Low GNI per capita = high vulnerability
    'inequality': 0.35,           # High Gini = high contagion risk
    'unemployment': 0.30          # High unemployment = high stress
}

COUNTRIES = ['ARG', 'TUR', 'EGY', 'PAK', 'NGA', 'BRA', 'ZAF', 'MEX', 'IDN', 'IND']


def get_sovereign_indicators() -> dict:
    """Fetch sovereign indicators from DynamoDB.

    The worldbank-poller writes signals with keys like:
      worldbank#ARG#NY.GNP.PCAP.CD#2023
    where 'value' is already a 0-100 signal score.
    """
    table = dynamodb.Table(SIGNALS_TABLE)
    indicators = defaultdict(lambda: {
        'gni_signal': None,
        'gini_signal': None,
        'unemployment_signal': None
    })

    try:
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'sovereign_dominoes'},
            ScanIndexForward=False,
            Limit=200
        )

        for item in response.get('Items', []):
            sort_key = item.get('signalId_timestamp', '')

            # Extract country from sort key (format: worldbank#COUNTRY#INDICATOR#DATE)
            parts = sort_key.split('#')
            if len(parts) >= 3:
                country = parts[1]

                # Value is already a 0-100 signal score from worldbank-poller
                signal_value = float(item.get('value', 0))

                if 'NY.GNP.PCAP.CD' in sort_key:
                    indicators[country]['gni_signal'] = signal_value
                elif 'SI.POV.GINI' in sort_key:
                    indicators[country]['gini_signal'] = signal_value
                elif 'SL.UEM.TOTL.ZS' in sort_key:
                    indicators[country]['unemployment_signal'] = signal_value

        logger.info(f"Found indicators for {len(indicators)} countries")
        return dict(indicators)

    except Exception as e:
        logger.error(f"Error fetching sovereign indicators: {str(e)}")
        return {}


def compute_country_risk_score(indicators: dict) -> float:
    """Compute risk score for a country (0-100).

    Indicators already arrive as 0-100 signal scores from worldbank-poller:
    - gni_signal: Low GNI = high score (high vulnerability)
    - gini_signal: High Gini = high score (high inequality)
    - unemployment_signal: High unemployment = high score (high stress)
    """
    risk_score = 0.0
    components_found = 0

    # GNI vulnerability (already 0-100, higher = more vulnerable)
    if indicators['gni_signal'] is not None:
        risk_score += indicators['gni_signal'] * WEIGHTS['gni_vulnerability']
        components_found += 1
        logger.info(f"  GNI contribution: {indicators['gni_signal'] * WEIGHTS['gni_vulnerability']:.2f}")

    # Inequality (already 0-100, higher = more unequal)
    if indicators['gini_signal'] is not None:
        risk_score += indicators['gini_signal'] * WEIGHTS['inequality']
        components_found += 1
        logger.info(f"  Gini contribution: {indicators['gini_signal'] * WEIGHTS['inequality']:.2f}")

    # Unemployment stress (already 0-100, higher = more stressed)
    if indicators['unemployment_signal'] is not None:
        risk_score += indicators['unemployment_signal'] * WEIGHTS['unemployment']
        components_found += 1
        logger.info(f"  Unemployment contribution: {indicators['unemployment_signal'] * WEIGHTS['unemployment']:.2f}")

    if components_found == 0:
        return 0.0

    # Scale up if not all components present
    if components_found < 3:
        total_weight = sum(
            w for k, w in WEIGHTS.items()
            if (k == 'gni_vulnerability' and indicators['gni_signal'] is not None) or
               (k == 'inequality' and indicators['gini_signal'] is not None) or
               (k == 'unemployment' and indicators['unemployment_signal'] is not None)
        )
        if total_weight > 0:
            risk_score = risk_score / total_weight * 1.0  # Normalize to available weights

    return min(100, max(0, risk_score))


def compute_regional_correlations(country_scores: dict) -> dict:
    """Compute contagion paths based on regional correlations."""
    # Regional groupings
    regions = {
        'latam': ['ARG', 'BRA', 'MEX'],
        'mideast_africa': ['TUR', 'EGY', 'NGA', 'ZAF'],
        'asia': ['PAK', 'IDN', 'IND']
    }

    contagion_paths = []

    # Find countries in same region with risk > 0.5
    for region, countries in regions.items():
        high_risk_countries = [c for c in countries if country_scores.get(c, 0) > 50]

        if len(high_risk_countries) >= 2:
            for i, country1 in enumerate(high_risk_countries):
                for country2 in high_risk_countries[i + 1:]:
                    contagion_paths.append({
                        'source': country1,
                        'target': country2,
                        'region': region,
                        'correlation': Decimal('0.65'),
                        'source_risk': Decimal(str(round(country_scores[country1], 2))),
                        'target_risk': Decimal(str(round(country_scores[country2], 2)))
                    })

    return {'contagion_paths': contagion_paths}


def write_risk_scores_to_state(risk_scores: dict, timestamp: str) -> None:
    """Write risk scores to dashboard state table."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        item = {
            'dashboard': 'sovereign_dominoes',
            'panel': 'risk_scores',
            'country_scores': {k: Decimal(str(round(v, 2))) for k, v in risk_scores.items()},
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        }
        table.put_item(Item=item)
        logger.info("Wrote risk scores to dashboard state table")
    except Exception as e:
        logger.error(f"Error writing risk scores: {str(e)}")
        raise


def compute_network_data(country_scores: dict, all_indicators: dict = None) -> dict:
    """Compute directed adjacency graph for network visualization (EV5-03 + EV7-DAG).

    Edges are directional: higher risk → lower risk (contagion flows from stressed economies).
    Nodes include vulnerability_score derived from GNI and Gini indicators.
    """
    regions = {
        'latam': ['ARG', 'BRA', 'MEX'],
        'mideast_africa': ['TUR', 'EGY', 'NGA', 'ZAF'],
        'asia': ['PAK', 'IDN', 'IND']
    }

    country_names = {
        'ARG': 'Argentina', 'BRA': 'Brazil', 'TUR': 'Turkey',
        'EGY': 'Egypt', 'PAK': 'Pakistan', 'NGA': 'Nigeria',
        'ZAF': 'South Africa', 'MEX': 'Mexico', 'IDN': 'Indonesia', 'IND': 'India'
    }

    # Build nodes with vulnerability scoring
    nodes = []
    for code, score in country_scores.items():
        region = next((r for r, cs in regions.items() if code in cs), 'other')

        # Vulnerability score: how susceptible to incoming contagion (GNI + Gini)
        vulnerability = Decimal('50')  # default
        if all_indicators and code in all_indicators:
            ind = all_indicators[code]
            gni = float(ind.get('gni_signal') or 50)
            gini = float(ind.get('gini_signal') or 50)
            vulnerability = Decimal(str(round((gni * 0.5 + gini * 0.5), 1)))

        nodes.append({
            'id': code,
            'name': country_names.get(code, code),
            'risk_score': Decimal(str(round(score, 2))),
            'vulnerability_score': vulnerability,
            'region': region,
            'status': 'critical' if score > 70 else 'warning' if score > 50 else 'monitoring'
        })

    # Build DIRECTED edges: higher risk → lower risk within regions
    edges = []
    for region, countries in regions.items():
        region_countries = [c for c in countries if c in country_scores]
        for i, c1 in enumerate(region_countries):
            for c2 in region_countries[i + 1:]:
                s1, s2 = country_scores[c1], country_scores[c2]

                # Direction: higher risk is source (contagion origin)
                if s1 >= s2:
                    source, target = c1, c2
                    source_risk, target_risk = s1, s2
                else:
                    source, target = c2, c1
                    source_risk, target_risk = s2, s1

                # Propagation strength = base correlation * risk differential multiplier
                avg_risk = (s1 + s2) / 2
                base_correlation = min(1.0, avg_risk / 100 * 1.3)
                risk_delta = abs(s1 - s2)
                propagation_strength = min(1.0, base_correlation * (1.0 + risk_delta / 100))

                if propagation_strength > 0.2:
                    edges.append({
                        'source': source,
                        'target': target,
                        'weight': Decimal(str(round(propagation_strength, 3))),
                        'region': region,
                        'source_risk': Decimal(str(round(source_risk, 2))),
                        'target_risk': Decimal(str(round(target_risk, 2))),
                        'risk_delta': Decimal(str(round(risk_delta, 2))),
                    })

    # Also add cross-regional edges for high-risk countries (risk > 60)
    high_risk_countries = [c for c, s in country_scores.items() if s > 60]
    for i, c1 in enumerate(high_risk_countries):
        for c2 in high_risk_countries[i + 1:]:
            # Check they're in different regions
            r1 = next((r for r, cs in regions.items() if c1 in cs), 'other')
            r2 = next((r for r, cs in regions.items() if c2 in cs), 'other')
            if r1 == r2:
                continue
            s1, s2 = country_scores[c1], country_scores[c2]
            if s1 >= s2:
                source, target = c1, c2
                source_risk, target_risk = s1, s2
            else:
                source, target = c2, c1
                source_risk, target_risk = s2, s1

            # Cross-regional edges are weaker (0.5x multiplier)
            avg_risk = (s1 + s2) / 2
            cross_weight = min(1.0, (avg_risk / 100 * 1.3) * 0.5)
            if cross_weight > 0.25:
                edges.append({
                    'source': source,
                    'target': target,
                    'weight': Decimal(str(round(cross_weight, 3))),
                    'region': 'cross_regional',
                    'source_risk': Decimal(str(round(source_risk, 2))),
                    'target_risk': Decimal(str(round(target_risk, 2))),
                    'risk_delta': Decimal(str(round(abs(s1 - s2), 2))),
                })

    return {'nodes': nodes, 'edges': edges}


def compute_cascade_probabilities(edges: list, country_scores: dict) -> dict:
    """Compute multi-hop cascade paths and their cumulative probabilities (EV7-DAG)."""
    # Build directed adjacency list
    graph = defaultdict(list)
    for edge in edges:
        src = edge.get('source', '')
        tgt = edge.get('target', '')
        w = float(edge.get('weight', 0))
        if src and tgt and w > 0:
            graph[src].append({'target': tgt, 'weight': w})

    paths = []
    # Find cascade paths from high-risk sources (risk > 55)
    for code, score in country_scores.items():
        if score > 55:
            _dfs_cascade(graph, code, [code], 1.0, paths, max_depth=3)

    # Sort by probability descending, take top 10
    paths.sort(key=lambda p: float(p['cumulative_probability']), reverse=True)
    return {'paths': paths[:10]}


def _dfs_cascade(graph, node, path, cum_prob, results, max_depth):
    """DFS to find cascade paths with cumulative probability."""
    if len(path) > max_depth:
        return
    for neighbor in graph.get(node, []):
        tgt = neighbor['target']
        if tgt not in path:
            new_prob = cum_prob * neighbor['weight']
            new_path = path + [tgt]
            if new_prob > 0.15 and len(new_path) >= 2:  # Only significant paths
                results.append({
                    'path': new_path,
                    'cumulative_probability': Decimal(str(round(new_prob, 3))),
                    'length': len(new_path)
                })
            _dfs_cascade(graph, tgt, new_path, new_prob, results, max_depth)


def write_network_data_to_state(network_data: dict, timestamp: str) -> None:
    """Write network visualization data to dashboard-state (EV5-03)."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)
    try:
        table.put_item(Item={
            'dashboard': 'sovereign_dominoes',
            'panel': 'network_data',
            'nodes': network_data['nodes'],
            'edges': network_data['edges'],
            'node_count': len(network_data['nodes']),
            'edge_count': len(network_data['edges']),
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        })
        logger.info(f"Wrote network data: {len(network_data['nodes'])} nodes, {len(network_data['edges'])} edges")
    except Exception as e:
        logger.error(f"Error writing network data: {e}")


def write_contagion_paths_to_state(contagion_data: dict, timestamp: str) -> None:
    """Write contagion paths to dashboard state table."""
    table = dynamodb.Table(DASHBOARD_STATE_TABLE)

    try:
        item = {
            'dashboard': 'sovereign_dominoes',
            'panel': 'contagion_paths',
            'paths': contagion_data['contagion_paths'],
            'path_count': len(contagion_data['contagion_paths']),
            'timestamp': timestamp,
            'last_updated': datetime.utcnow().isoformat()
        }
        table.put_item(Item=item)
        logger.info(f"Wrote {len(contagion_data['contagion_paths'])} contagion paths to state table")
    except Exception as e:
        logger.error(f"Error writing contagion paths: {str(e)}")
        raise


def publish_event(risk_scores: dict, contagion_paths: int, timestamp: str) -> None:
    """Publish EventBridge event."""
    try:
        max_risk_country = max(risk_scores.items(), key=lambda x: x[1])[0] if risk_scores else None
        max_risk_score = max(risk_scores.values()) if risk_scores else 0

        event = {
            'Source': 'mvt.processing.score',
            'DetailType': 'ContagionScoreUpdated',
            'EventBusName': EVENT_BUS_NAME,
            'Detail': json.dumps({
                'countries_analyzed': len(risk_scores),
                'max_risk_country': max_risk_country,
                'max_risk_score': round(max_risk_score, 2),
                'contagion_paths': contagion_paths,
                'timestamp': timestamp
            })
        }
        events.put_events(Entries=[event])
        logger.info(f"Published ContagionScoreUpdated event ({len(risk_scores)} countries, {contagion_paths} paths)")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda handler."""
    logger.info("Starting contagion modeler")

    try:
        timestamp = datetime.utcnow().isoformat()

        # Fetch sovereign indicators
        logger.info("Fetching sovereign indicators")
        all_indicators = get_sovereign_indicators()

        # Compute risk scores for each country
        risk_scores = {}
        for country in COUNTRIES:
            if country in all_indicators:
                risk_score = compute_country_risk_score(all_indicators[country])
                risk_scores[country] = risk_score
                logger.info(f"{country} risk score: {risk_score:.2f}")

        # Compute contagion paths
        logger.info("Computing contagion paths")
        contagion_data = compute_regional_correlations(risk_scores)

        # Compute directed network data for DAG visualization (EV5-03 + EV7-DAG)
        logger.info("Computing directed network data with vulnerability scores")
        network_data = compute_network_data(risk_scores, all_indicators)

        # Compute cascade probabilities for multi-hop contagion paths (EV7-DAG)
        logger.info("Computing cascade probabilities")
        cascade_data = compute_cascade_probabilities(network_data['edges'], risk_scores)
        logger.info(f"Found {len(cascade_data['paths'])} significant cascade paths")

        # Write to state tables
        write_risk_scores_to_state(risk_scores, timestamp)
        write_contagion_paths_to_state(contagion_data, timestamp)
        write_network_data_to_state(network_data, timestamp)

        # Write cascade probabilities
        try:
            table = dynamodb.Table(DASHBOARD_STATE_TABLE)
            table.put_item(Item={
                'dashboard': 'sovereign_dominoes',
                'panel': 'cascade_probabilities',
                'paths': cascade_data['paths'],
                'path_count': len(cascade_data['paths']),
                'timestamp': timestamp,
                'last_updated': datetime.utcnow().isoformat()
            })
            logger.info(f"Wrote {len(cascade_data['paths'])} cascade probability paths")
        except Exception as e:
            logger.error(f"Error writing cascade probabilities: {e}")

        # Publish event
        publish_event(risk_scores, len(contagion_data['contagion_paths']), timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Contagion modeling completed',
                'countries_analyzed': len(risk_scores),
                'contagion_paths': len(contagion_data['contagion_paths']),
                'cascade_paths': len(cascade_data['paths']),
                'network_nodes': len(network_data['nodes']),
                'network_edges': len(network_data['edges'])
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# Alias for Lambda handler configuration
handler = lambda_handler
