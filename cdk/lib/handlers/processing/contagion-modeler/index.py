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

# Risk score weights
WEIGHTS = {
    'reserve_adequacy': 0.30,
    'debt_sustainability': 0.25,
    'currency_pressure': 0.25,
    'gdp_trajectory': 0.20
}

COUNTRIES = ['ARG', 'TUR', 'EGY', 'PAK', 'NGA', 'BRA', 'ZAF', 'MEX', 'IDN', 'IND']


def get_sovereign_indicators() -> dict:
    """Fetch sovereign indicators from DynamoDB."""
    table = dynamodb.Table(SIGNALS_TABLE)
    indicators = defaultdict(lambda: {
        'reserves': None,
        'external_debt': None,
        'fx_depreciation': None,
        'gdp_growth': None
    })

    try:
        response = table.query(
            KeyConditionExpression='dashboard = :dashboard',
            ExpressionAttributeValues={':dashboard': 'sovereign_dominoes'},
            ScanIndexForward=False,
            Limit=200
        )

        for item in response.get('Items', []):
            sort_key = item.get('sort_key', '')

            # Extract country from sort key
            parts = sort_key.split('#')
            if len(parts) >= 2:
                country = parts[1]

                if 'FI.RES.TOTL.CD' in sort_key:
                    indicators[country]['reserves'] = float(item.get('value', 0))
                elif 'DT.DOD.DECT.GN.ZS' in sort_key:
                    indicators[country]['external_debt'] = float(item.get('value', 0))
                elif 'NY.GDP.MKTP.KD.ZG' in sort_key:
                    indicators[country]['gdp_growth'] = float(item.get('value', 0))
                elif 'USD' in sort_key and '=X' in sort_key:
                    # FX data - store for depreciation calculation
                    indicators[country]['fx_depreciation'] = float(item.get('monthly_change_pct', 0))

        return dict(indicators)

    except Exception as e:
        logger.error(f"Error fetching sovereign indicators: {str(e)}")
        return {}


def normalize_value(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to 0-100 scale."""
    if value is None or max_val == min_val:
        return 0.0

    normalized = ((value - min_val) / (max_val - min_val)) * 100
    return max(0, min(100, normalized))


def compute_reserve_adequacy(reserves: float) -> float:
    """Compute reserve adequacy score (higher reserves = lower risk)."""
    if reserves is None or reserves <= 0:
        return 100.0  # Max risk if no reserves

    # Normalize based on adequacy threshold (USD billions)
    # Higher values = more reserves = lower risk
    reserve_score = normalize_value(reserves, 10_000_000_000, 500_000_000_000)
    return 100 - reserve_score  # Invert: more reserves = lower risk


def compute_debt_sustainability(external_debt: float) -> float:
    """Compute debt sustainability score (0-100% GNI)."""
    if external_debt is None:
        return 50.0  # Neutral if unknown

    # Normalize 0-80% GNI range
    return normalize_value(external_debt, 0, 80)


def compute_currency_pressure(fx_depreciation: float) -> float:
    """Compute currency pressure score (30-day depreciation)."""
    if fx_depreciation is None:
        return 0.0

    # Positive depreciation = pressure; normalize 0-30% range
    return normalize_value(abs(fx_depreciation), 0, 30)


def compute_gdp_trajectory(gdp_growth: float) -> float:
    """Compute GDP trajectory score (negative growth = risk)."""
    if gdp_growth is None:
        return 50.0  # Neutral if unknown

    # Normalize -5% to +5% range
    # Negative growth = risk
    trajectory = normalize_value(gdp_growth, -5, 5)
    return 100 - trajectory  # Invert: growth reduces risk


def compute_country_risk_score(indicators: dict) -> float:
    """Compute risk score for a country (0-100)."""
    risk_score = 0.0

    # Reserve adequacy
    reserve_score = compute_reserve_adequacy(indicators['reserves'])
    risk_score += reserve_score * WEIGHTS['reserve_adequacy']
    logger.debug(f"Reserve adequacy contribution: {reserve_score * WEIGHTS['reserve_adequacy']:.2f}")

    # Debt sustainability
    debt_score = compute_debt_sustainability(indicators['external_debt'])
    risk_score += debt_score * WEIGHTS['debt_sustainability']
    logger.debug(f"Debt sustainability contribution: {debt_score * WEIGHTS['debt_sustainability']:.2f}")

    # Currency pressure
    fx_score = compute_currency_pressure(indicators['fx_depreciation'])
    risk_score += fx_score * WEIGHTS['currency_pressure']
    logger.debug(f"Currency pressure contribution: {fx_score * WEIGHTS['currency_pressure']:.2f}")

    # GDP trajectory
    gdp_score = compute_gdp_trajectory(indicators['gdp_growth'])
    risk_score += gdp_score * WEIGHTS['gdp_trajectory']
    logger.debug(f"GDP trajectory contribution: {gdp_score * WEIGHTS['gdp_trajectory']:.2f}")

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
                        'correlation': 0.65,  # Simplified: countries in same region correlate at 0.65
                        'source_risk': float(country_scores[country1]),
                        'target_risk': float(country_scores[country2])
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

        # Write to state tables
        write_risk_scores_to_state(risk_scores, timestamp)
        write_contagion_paths_to_state(contagion_data, timestamp)

        # Publish event
        publish_event(risk_scores, len(contagion_data['contagion_paths']), timestamp)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Contagion modeling completed',
                'countries_analyzed': len(risk_scores),
                'contagion_paths': len(contagion_data['contagion_paths'])
            })
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
