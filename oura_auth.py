#!/usr/bin/env python3
"""One-time Oura OAuth authorization (manual code copy — no callback server).

Steps:
    1) python oura_auth.py url
       -> prints the consent URL. Open it, click Allow.
       Oura redirects your browser to the GitHub repo URL with ?code=XXXX in
       the address bar (the page content itself doesn't matter).
    2) python oura_auth.py code XXXX
       -> exchanges the code for tokens and saves oura_tokens.json.
    3) python oura_auth.py test
       -> pulls today's Bio-Log metrics to confirm it works end to end.
"""
import sys
import urllib.parse

import oura_client


def main() -> int:
    args = sys.argv[1:]
    secrets = oura_client.load_secrets()

    if args and args[0] == "url":
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": secrets["client_id"],
            "redirect_uri": secrets["redirect_uri"],
            "scope": oura_client.OURA_SCOPES,
            "state": "logfromwatch",
        })
        print(oura_client.OURA_AUTHORIZE_URL + "?" + params)
        return 0

    if len(args) >= 2 and args[0] == "code":
        oura_client.OuraAuth(secrets).exchange_code(args[1])
        print(f"Saved tokens to {oura_client.TOKENS_FILE}")
        return 0

    if args and args[0] == "test":
        from datetime import datetime
        auth = oura_client.OuraAuth(secrets)
        token = auth.get_access_token()
        day = datetime.now().strftime("%Y-%m-%d")
        responses = oura_client.fetch_oura_day(token, day)
        metrics = oura_client.build_bio_log(responses, day)
        print("Metrics:", metrics)
        print("Has real data:", oura_client.has_real_data(metrics))
        print("\n" + oura_client.render_bio_log_table(metrics, datetime.now().strftime("%H:%M")))
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
