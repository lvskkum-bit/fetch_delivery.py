name: NSE Delivery Cache

on:
  workflow_dispatch:

  schedule:
    # 12:45 UTC = 6:15 PM IST
    - cron: "45 12 * * 1-5"

permissions:
  contents: write

jobs:
  delivery:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install requests
        run: pip install requests

      - name: Fetch NSE delivery
        run: python fetch_delivery.py

      - name: Commit delivery cache
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"

          git add delivery_latest.json

          git diff --cached --quiet && exit 0

          git commit -m "Update NSE delivery cache"

          git push
