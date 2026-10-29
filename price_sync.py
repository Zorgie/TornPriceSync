#!/usr/bin/env python3
"""Copy a Torn price list from one site to the other (weav3r.dev <-> tornexchange.com).

Both read APIs only return the resolved absolute price, so prices are written to the
target as fixed prices, unless --percent is given: then each price is converted to a
percentage of the Torn market value (weav3r: market_percentage, tornexchange: discount).
Items missing from the source are left untouched on the target.

Example:
    python price_sync.py --from weav3r --weav3r-key W_KEY --te-key T_KEY --torn-id 12345 --percent --torn-key TORN_KEY --dry-run
"""
import argparse
import json
import urllib.request

WEAV3R = "https://weav3r.dev/api/pricelist/{id}"
TE_READ = "https://tornexchange.com/api/prices/{id}?key={key}"
TE_WRITE = "https://tornexchange.com/api/modify_listing"
TORN_ITEMS = "https://api.torn.com/v2/torn/items?cat=All&sort=ASC&key={key}"


def call(method, url, headers=None, body=None):
    # weav3r.dev sits behind Cloudflare, which rejects urllib's default User-Agent
    headers = {"User-Agent": "price-sync/1.0", "Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def read_weav3r(args):
    items = call("GET", WEAV3R.format(id=args.torn_id), {"X-API-Key": args.weav3r_key})
    return {i["itemId"]: i["buyPrice"] for i in items}


def read_te(args):
    data = call("GET", TE_READ.format(id=args.torn_id, key=args.te_key))["data"]
    return {i["item_id"]: i["price"] for i in data["items"]}


def read_market_values(args):
    items = call("GET", TORN_ITEMS.format(key=args.torn_key))["items"]
    return {i["id"]: i["value"]["market_price"] for i in items}


def build_weav3r_update(args, prices, market):
    items = []
    for item, price in prices.items():
        percent = round(price / market[item] * 100, 2) if market.get(item) else None
        if percent is not None:
            items.append({"itemID": item, "pricingType": "market_percentage", "pricingValue": percent})
        else:
            items.append({"itemID": item, "pricingType": "fixed", "pricingValue": price})
    return "PUT", WEAV3R.format(id=args.torn_id), {"X-API-Key": args.weav3r_key}, {"items": items}


def build_te_update(args, prices, market):
    listings = []
    for item, price in prices.items():
        if market.get(item):
            listings.append({"item_id": item, "discount": round(100 - price / market[item] * 100, 2)})
        else:
            listings.append({"item_id": item, "fixed_price": price})
    return "POST", TE_WRITE, {}, {"key": args.te_key, "action": "update", "listings": listings}


def redact(value, secrets):
    text = json.dumps(value, indent=2)
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", choices=["weav3r", "tornexchange"], required=True,
                        help="site to read the price list from; the other site is updated")
    parser.add_argument("--weav3r-key", required=True)
    parser.add_argument("--te-key", required=True)
    parser.add_argument("--torn-id", type=int, required=True)
    parser.add_argument("--percent", action="store_true",
                        help="write percentages of the Torn market value instead of fixed prices (needs --torn-key)")
    parser.add_argument("--torn-key", help="torn.com API key, used to fetch market values for --percent")
    parser.add_argument("--dry-run", action="store_true", help="read the source and print the update request, but don't send it")
    args = parser.parse_args()
    if args.percent and not args.torn_key:
        parser.error("--percent requires --torn-key")

    if args.source == "weav3r":
        prices, build_update = read_weav3r(args), build_te_update
    else:
        prices, build_update = read_te(args), build_weav3r_update

    prices = {item: price for item, price in prices.items() if price and price > 0}
    print(f"Read {len(prices)} priced items from {args.source}")

    market = read_market_values(args) if args.percent else {}
    method, url, headers, body = build_update(args, prices, market)
    if args.dry_run:
        print(f"[dry run] {method} {url.replace(args.te_key, '***')}")
        print(redact(body, [args.te_key, args.weav3r_key, args.torn_key or ""]))
        return

    print(json.dumps(call(method, url, headers, body), indent=2))


if __name__ == "__main__":
    main()
