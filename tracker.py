import requests
import time
import random
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import logging
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tracker.log"),
        logging.StreamHandler()
    ]
)

class StockTracker:
    def __init__(self, proxies_file='proxies.txt'):
        self.ua = UserAgent()
        self.proxies = self._load_proxies(proxies_file)
        self.session = requests.Session()

    def send_notification(self, subject, body):
        webhook_url = os.environ.get('NOTIFICATION_URL')
        
        if not webhook_url:
            return

        try:
            # Check if it's a known service format or generic
            if "ntfy.sh" in webhook_url:
                requests.post(webhook_url,
                    data=body.encode(encoding='utf-8'),
                    headers={
                        "Title": subject,
                        "Priority": "high",
                        "Tags": "tada"
                    })
            else:
                 # Generic Webhook (Discord, Slack, IFTTT)
                 # Most expect JSON
                 payload = {"content": f"**{subject}**\n{body}"} # Discordish format
                 if "maker.ifttt.com" in webhook_url:
                      payload = {"value1": subject, "value2": body}
                 
                 requests.post(webhook_url, json=payload)
                 
            logging.info(f"Notification sent to webhook")
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")

    def _load_proxies(self, filepath):
        proxies = []
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        proxies.append(line)
        except FileNotFoundError:
            logging.warning(f"{filepath} not found. Running without proxies.")
        return proxies

    def _get_random_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1',  # Do Not Track
        }

    def _get_random_proxy(self):
        if not self.proxies:
            return None
        proxy_url = random.choice(self.proxies)
        return {
            "http": proxy_url,
            "https": proxy_url
        }

    def fetch_url(self, url):
        headers = self._get_random_headers()
        proxies = self._get_random_proxy()
        
        # Random sleep to avoid pattern detection
        sleep_time = random.uniform(2, 5) 
        # logging.info(f"Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

        try:
            # logging.info(f"Fetching {url} with proxy: {'Yes' if proxies else 'No'}")
            response = self.session.get(
                url, 
                headers=headers, 
                proxies=proxies, 
                timeout=15
            )
            
            if response.status_code == 200:
                return response.text
            elif response.status_code == 429:
                logging.warning("Rate limited! Sleeping for longer...")
                time.sleep(random.uniform(30, 60))
                return None
            else:
                logging.error(f"Failed to fetch {url}. Status code: {response.status_code}")
                return None

        except Exception as e:
            logging.error(f"Error fetching {url}: {e}")
            return None

    def construct_api_url(self, retailer, sku, zipcode):
        return f"https://api.snormax.com/stock/{retailer}?sku={sku}&zip={zipcode}"

    def parse_data(self, json_data):
        if not json_data:
            return []
        
        results = []
        try:
            # Map location IDs to location details
            loc_map = {loc['id']: loc for loc in json_data.get('locations', [])}
            
            items = json_data.get('items', [])
            if not items:
                return []
                
            # Usually we monitor one SKU, so items[0]
            item_locations = items[0].get('locations', [])
            
            for item_loc in item_locations:
                loc_id = item_loc.get('locationId')
                loc_details = loc_map.get(loc_id)
                
                if not loc_details:
                    continue
                
                # Check quantities
                pickup_qty = item_loc.get('availability', {}).get('availablePickupQuantity', 0)
                instore_qty = item_loc.get('inStoreAvailability', {}).get('availableInStoreQuantity', 0)
                
                # Treat None as 0
                if pickup_qty is None: pickup_qty = 0
                if instore_qty is None: instore_qty = 0
                
                total_qty = max(pickup_qty, instore_qty)
                
                # Calculate distance if available (distance from zipcode center)
                distance = loc_details.get('distance', 'N/A')

                results.append({
                    'store_name': loc_details.get('name'),
                    'address': f"{loc_details.get('address')}, {loc_details.get('city')}, {loc_details.get('state')}",
                    'distance': distance,
                    'stock': total_qty,
                    'is_pickup': pickup_qty > 0,
                    'is_instore': instore_qty > 0
                })
                
        except Exception as e:
            logging.error(f"Error parsing JSON: {e}")
            return []
                        
        return results

    def run(self, targets):
        """
        targets: list of dicts with keys 'retailer', 'sku', 'zipcode'
        """
        in_stock_items = []
        
        for target in targets:
            retailer = target.get('retailer', 'bestbuy') # Default to bestbuy
            sku = target['sku']
            name = target.get('name', sku)
            zipcode = target['zipcode']
            
            url = self.construct_api_url(retailer, sku, zipcode)
            # Minimal feedback to show it's working
            # print(f"Checking {name}...", end='\r')
            
            response_text = self.fetch_url(url)
            if response_text:
                import json
                try:
                    data = json.loads(response_text)
                    parsed_results = self.parse_data(data)
                    
                    in_stock = [item for item in parsed_results if item['stock'] > 0]
                    
                    # Filter by distance (<= 10 miles)
                    nearby_stock = []
                    for item in in_stock:
                        dist = item.get('distance')
                        if isinstance(dist, (int, float)) and dist <= 10:
                            nearby_stock.append(item)
                    
                    if nearby_stock:
                        total_stock = sum(item['stock'] for item in nearby_stock)
                        in_stock_items.append({'name': name, 'count': total_stock})
                        
                except json.JSONDecodeError:
                    logging.error("Failed to decode JSON response")

        if in_stock_items:
            print("\nThe following products are in stock:")
            msg_lines = []
            for item in in_stock_items:
                line = f"- {item['name']} (Total: {item['count']})"
                print(line)
                msg_lines.append(line)
            
            # Send notification
            if in_stock_items:
                body = "Stock found for the following items:\n\n" + "\n".join(msg_lines)
                body += f"\n\nZip Code: {targets[0]['zipcode']}"
                self.send_notification("Pokemon Stock Alert!", body)
        else:
            print("\nNone of the products are in stock within 10 miles.")

def load_skus(filepath):
    items = []
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Split by comma if present
                    if ',' in line:
                        parts = line.split(',', 1)
                        items.append({'sku': parts[0].strip(), 'name': parts[1].strip()})
                    else:
                        items.append({'sku': line, 'name': line})
    except FileNotFoundError:
        logging.error(f"{filepath} not found.")
    return items
            
if __name__ == "__main__":
    # Configuration
    ZIP_CODE = "94568"
    SKU_FILE = "skus.txt"
    
    # Load SKUs from file
    items = load_skus(SKU_FILE)
    if not items:
        logging.error("No SKUs loaded. Exiting.")
        exit(1)
        
    logging.info(f"Loaded {len(items)} items from {SKU_FILE}")
    
    # Build targets
    targets = [
        {'retailer': 'bestbuy', 'sku': item['sku'], 'name': item['name'], 'zipcode': ZIP_CODE}
        for item in items
    ]
    
    tracker = StockTracker()
    tracker.run(targets)
