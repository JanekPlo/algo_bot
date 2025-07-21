import requests
import pandas as pd

def fetch_historical_fear_greed(limit=1000):
    """
    Fetch historical Crypto Fear and Greed Index data.
    
    Args:
        limit (int): Number of records to fetch (max: 1000).

    Returns:
        DataFrame: Historical data as a Pandas DataFrame.
    """
    url = f"https://api.alternative.me/fng/?limit={limit}&format=json"
    response = requests.get(url)
    data = response.json()
    
    # Parse data into DataFrame
    df = pd.DataFrame(data["data"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df[["timestamp", "value", "value_classification"]]
    df.columns = ["Date", "FearGreedIndex", "Classification"]
    df["FearGreedIndex"] = df["FearGreedIndex"].astype(int)
    
    return df

# Fetch data
historical_data = fetch_historical_fear_greed()

# Save to CSV
historical_data.to_csv("crypto_fear_greed.csv", index=False)
print("Historical Crypto Fear and Greed Index data saved to 'crypto_fear_greed.csv'")
