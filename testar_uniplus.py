import requests

OAUTH_URL = "https://vzan-getcard01.getcard.uniplusweb.com/oauth/token"
API_URL   = "https://vzan-getcard01.getcard.uniplusweb.com/api/rest"
AUTH_CODE = "NDAwNDUxNTIwMDAxMjU6Y2JlZWUyZTgtNmQ1YS00MGUwLWFmNTMtZGVjNjI3OTdlMzZh"

print("=" * 55)
print("  TESTE UNIPLUS — TESTANDO SCOPES")
print("=" * 55)

for scope in ["web", "public-api web", "web public-api", "read write"]:
    print(f"\n[scope: {scope}]")
    r = requests.post(OAUTH_URL,
        headers={"Authorization": f"Basic {AUTH_CODE}", "Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=client_credentials&scope={scope}", timeout=10)
    print(f"  Token status: {r.status_code}")
    if r.status_code == 200:
        token = r.json().get("access_token")
        scope_ret = r.json().get("scope")
        print(f"  Scope retornado: {scope_ret}")
        H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r2 = requests.get(API_URL + "/public-api/v1/entidades", headers=H, params={"limit": 1}, timeout=10)
        print(f"  Entidades: {r2.status_code} | {r2.text[:150]}")
        if r2.status_code == 200:
            print(f"  FUNCIONOU com scope '{scope}'!")
            break
    else:
        print(f"  {r.text[:100]}")

print("\n" + "=" * 55)
