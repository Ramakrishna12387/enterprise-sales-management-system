"""Enterprise SMS - Streamlit frontend.  Flow: Streamlit -> Python -> Apps Script API -> Google Sheets."""
import json, re
from datetime import date
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="Enterprise SMS", page_icon="📊", layout="wide")
NAVY = "#0b2a8c"
st.markdown(f"""<style>
[data-testid=stSidebar]{{background:#f3f4fb}}
div[data-testid=stVerticalBlockBorderWrapper]{{border-radius:8px}}
[data-testid=stMetricValue]{{font-family:monospace;color:{NAVY}}}
h1,h2,h3{{color:#111a4d}}
</style>""", unsafe_allow_html=True)

MODES = ["Cash", "UPI", "Cheque", "Card", "Bank Transfer"]
STATUS_ICON = {"PAID": "🟢 PAID", "PARTIAL": "🟣 PARTIAL", "PENDING": "🔴 PENDING"}


# ---------------------------------------------------------------- API layer
class ApiError(Exception):
    pass


def _post(action, params, token):
    payload = {"api_key": st.secrets["API_KEY"], "action": action, "params": params or {}, "token": token}
    r = requests.post(st.secrets["APPS_SCRIPT_URL"], json=payload, timeout=60)
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        raise ApiError(d.get("error", "Unknown error"))
    return d["data"]


@st.cache_data(ttl=30, show_spinner=False)
def _cached(action, params_json, token):
    return _post(action, json.loads(params_json), token)


def get(action, **params):
    """Cached read."""
    try:
        return _cached(action, json.dumps(params, sort_keys=True), st.session_state.get("token"))
    except ApiError as e:
        if "session" in str(e).lower() or "logged in" in str(e).lower():
            st.session_state.clear(); st.rerun()
        raise


def write(action, **params):
    """Uncached write; clears read cache."""
    res = _post(action, params, st.session_state.get("token"))
    st.cache_data.clear()
    return res


def inr(v):
    n = int(round(float(v or 0))); s = str(abs(n))
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return ("-" if n < 0 else "") + "₹" + s


def df(rows, cols=None):
    d = pd.DataFrame(rows)
    return d[[c for c in cols if c in d.columns]] if cols and not d.empty else d


def fmt_ledger(rows):
    d = df(rows, ["sale_id", "branch", "customer", "gstin", "total", "received", "pending", "status", "date", "created_by"])
    if d.empty:
        return d
    d["status"] = d["status"].map(STATUS_ICON)
    return d.rename(columns={"sale_id": "TX ID", "total": "Invoiced (₹)", "received": "Received (₹)", "pending": "Pending (₹)"})


def guard(fn):
    try:
        fn()
    except ApiError as e:
        st.error(str(e))
    except requests.RequestException as e:
        st.error(f"Cannot reach the Apps Script API: {e}")


# ---------------------------------------------------------------- login
def login_view():
    st.title("Enterprise SMS")
    st.caption("Multi-branch sales, split payments & financial tracking")
    with st.form("login"):
        u = st.text_input("Username"); p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                d = _post("login", {"username": u, "password": p}, None)
                st.session_state.update(token=d["token"], user=d["user"]); st.rerun()
            except ApiError as e:
                st.error(str(e))
            except requests.RequestException as e:
                st.error(f"Cannot reach the API: {e}")


if "token" not in st.session_state:
    login_view(); st.stop()

USER = st.session_state["user"]; ADMIN = USER["role"] == "admin"
branches = get("list", entity="Branches")
BR = {b["branch_id"]: b["name"] for b in branches}

PAGES = ["Dashboard", "Branches", "Customers", "Products", "Sales Ledger", "Split Payments", "Payment Tracking",
         "Financial Tracking", "Sales Analytics", "Audit Reports"] + (["Users & Roles"] if ADMIN else [])
with st.sidebar:
    st.markdown("### Enterprise SMS")
    st.caption(f"{USER['username']} · {USER['role'].title()}" + ("" if ADMIN else f" · {BR.get(USER['branch_id'], USER['branch_id'])}"))
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    if st.button("Refresh data"): st.cache_data.clear(); st.rerun()
    if st.button("Sign out"): st.session_state.clear(); st.rerun()


def branch_filter(key):
    if not ADMIN:
        return None
    o = st.selectbox("Branch", ["All Branches (Consolidated)"] + list(BR), format_func=lambda x: BR.get(x, x), key=key)
    return None if o.startswith("All") else o


# ---------------------------------------------------------------- Dashboard
def p_dashboard():
    st.title("Executive Revenue & Branch Liquidity Overview")
    b = branch_filter("dash_b")
    d = get("dashboard", branch_id=b) if b else get("dashboard")
    k = d["kpi"]; st.caption(f"Fiscal period {d['range']['from']} → {d['range']['to']}")
    c = st.columns(4)
    for col, (l, v, sub) in zip(c, [("Total Branches", k["branches"], None), ("Catalog SKUs", k["skus"], f"{k['low_stock']} low stock"),
                                     ("Active Accounts", k["customers"], None), ("Gross Transactions", inr(k["gross"]), f"{k['invoices']} invoices")]):
        with col.container(border=True): st.metric(l, v, sub, delta_color="off")
    c = st.columns(4)
    for col, (l, v, sub) in zip(c, [("Booked Revenue", inr(k["gross"]), f"Avg ticket {inr(k['avg_ticket'])}"),
                                     ("Settled Amount", inr(k["settled"]), f"{k['collected_pct']}% collected"),
                                     ("Receivables Pending", inr(k["pending"]), None),
                                     ("Today's Inflow", inr(k["inflow_today"]), f"{k['invoices_today']} invoices today")]):
        with col.container(border=True): st.metric(l, v, sub, delta_color="off")
    l, r = st.columns([3, 2])
    with l.container(border=True):
        st.subheader("Consolidated Sales Trajectory")
        m = pd.DataFrame(d["monthly"]); m["Actual"] = m["revenue"].cumsum(); m["Target"] = m["target"].cumsum()
        f = go.Figure([go.Scatter(x=m.month, y=m.Actual, name="Actual", fill="tozeroy", line=dict(color=NAVY)),
                       go.Scatter(x=m.month, y=m.Target, name="Target", line=dict(color="#a5a9f5", dash="dash"))])
        f.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h"))
        st.plotly_chart(f, use_container_width=True)
    with r.container(border=True):
        st.subheader("Branch Revenue Matrix")
        for x in d["branch_matrix"]:
            st.write(f"**{x['name']}** — {inr(x['revenue'])}  (target {inr(x['target'])})")
            st.progress(min(x["achieved_pct"] / 100, 1.0), text=f"{x['achieved_pct']}% achieved")
    l, r = st.columns([3, 2])
    with l.container(border=True):
        st.subheader("Settlement vs Receivables Due")
        f = go.Figure([go.Bar(y=[""], x=[k["settled"]], name="Settled", orientation="h", marker_color="#0d5c3a"),
                       go.Bar(y=[""], x=[k["pending"]], name="Due", orientation="h", marker_color="#b91c1c")])
        f.update_layout(barmode="stack", height=130, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h"))
        st.plotly_chart(f, use_container_width=True)
        st.caption(f"{k['collected_pct']}% net settled")
    with r.container(border=True):
        st.subheader("Payment Tranche Breakdown")
        t = d["tranches"]
        f = px.pie(names=list(t), values=[v["count"] for v in t.values()], hole=.6,
                   color=list(t), color_discrete_map={"PAID": "#0d5c3a", "PARTIAL": "#4f46e5", "PENDING": "#b91c1c"})
        f.update_layout(height=230, margin=dict(l=0, r=0, t=0, b=0)); st.plotly_chart(f, use_container_width=True)
    with st.container(border=True):
        st.subheader("Branch Transactions & Settlement Ledger")
        st.dataframe(fmt_ledger(d["recent"]), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------- master data pages
def crud(entity, title, cols, fields, idk, can_edit=True):
    st.title(title)
    rows = get("list", entity=entity)
    st.dataframe(df(rows, cols), use_container_width=True, hide_index=True)
    if not can_edit:
        return
    with st.expander("➕ Add / edit", expanded=not rows):
        pick = st.selectbox("Record", ["<New>"] + [r[idk] for r in rows],
                            format_func=lambda x: x if x == "<New>" else f"{x} — {next(r for r in rows if r[idk] == x).get('name', '')}", key=f"pick_{entity}")
        cur = next((r for r in rows if r[idk] == pick), {})
        with st.form(f"f_{entity}_{pick}"):
            rec = {}
            for key, label, kind in fields:
                v = cur.get(key, "")
                if kind == "text": rec[key] = st.text_input(label, str(v))
                elif kind == "number": rec[key] = st.number_input(label, min_value=0.0, value=float(v or 0))
                elif kind == "bool": rec[key] = st.checkbox(label, value=(str(v).lower() == "true") if v != "" else True)
                elif kind == "branch":
                    opts = [""] + list(BR); rec[key] = st.selectbox(label, opts, index=opts.index(v) if v in opts else 0, format_func=lambda x: BR.get(x, "— none —"))
            if st.form_submit_button("Save", type="primary"):
                if pick != "<New>": rec[idk] = pick
                try:
                    write("save", entity=entity, record=rec); st.success("Saved"); st.rerun()
                except ApiError as e:
                    st.error(str(e))


def p_branches():
    crud("Branches", "Branch Management", ["branch_id", "name", "city", "manager", "target", "active"],
         [("name", "Name", "text"), ("city", "City", "text"), ("manager", "Manager", "text"), ("target", "Fiscal target (₹)", "number"), ("active", "Active", "bool")],
         "branch_id", can_edit=ADMIN)


def p_customers():
    f = [("name", "Name", "text"), ("gstin", "GSTIN", "text"), ("phone", "Phone", "text"), ("email", "Email", "text"), ("active", "Active", "bool")]
    if ADMIN: f.insert(4, ("branch_id", "Branch", "branch"))
    crud("Customers", "Customers", ["customer_id", "name", "gstin", "phone", "email", "branch_id", "active"], f, "customer_id")


def p_products():
    crud("Products", "Products & Inventory", ["product_id", "sku", "name", "price", "stock", "reorder_level", "branch_id", "active"],
         [("sku", "SKU", "text"), ("name", "Name", "text"), ("price", "Price (₹)", "number"), ("stock", "Stock", "number"),
          ("reorder_level", "Reorder level", "number"), ("branch_id", "Branch", "branch"), ("active", "Active", "bool")], "product_id", can_edit=ADMIN)


# ---------------------------------------------------------------- sales
def new_sale_form():
    custs = [c for c in get("list", entity="Customers") if str(c["active"]).lower() == "true"]
    if not custs or not branches:
        st.info("Add at least one branch and customer first."); return
    br = st.selectbox("Branch", list(BR), format_func=BR.get) if ADMIN else USER["branch_id"]
    cust = st.selectbox("Customer", [c["customer_id"] for c in custs if not c["branch_id"] or c["branch_id"] == br],
                        format_func=lambda x: next(c["name"] for c in custs if c["customer_id"] == x))
    prods = [p for p in get("list", entity="Products") if str(p["active"]).lower() == "true" and (not p["branch_id"] or p["branch_id"] == br)]
    label = {f"{p['sku']} – {p['name']} (stock {p['stock']})": p for p in prods}
    st.markdown("**Items**")
    it = st.data_editor(pd.DataFrame({"Product": pd.Series(dtype=str), "Qty": pd.Series(dtype=float)}), num_rows="dynamic", key=f"items_{br}",
                        column_config={"Product": st.column_config.SelectboxColumn(options=list(label), required=True),
                                       "Qty": st.column_config.NumberColumn(min_value=1, required=True)}, use_container_width=True)
    items = [{"product_id": label[r.Product]["product_id"], "qty": r.Qty} for r in it.itertuples() if r.Product in label and r.Qty]
    total = sum(label[r.Product]["price"] * r.Qty for r in it.itertuples() if r.Product in label and r.Qty)
    st.markdown("**Split payments** (leave empty for credit sale)")
    py = st.data_editor(pd.DataFrame({"Mode": pd.Series(dtype=str), "Amount": pd.Series(dtype=float), "Reference": pd.Series(dtype=str)}),
                        num_rows="dynamic", key=f"pays_{br}", use_container_width=True,
                        column_config={"Mode": st.column_config.SelectboxColumn(options=MODES, required=True),
                                       "Amount": st.column_config.NumberColumn(min_value=1, required=True)})
    pays = [{"mode": r.Mode, "amount": r.Amount, "reference": r.Reference or ""} for r in py.itertuples() if r.Mode and r.Amount]
    rec = sum(p["amount"] for p in pays)
    c = st.columns(3); c[0].metric("Invoice total", inr(total)); c[1].metric("Received", inr(rec)); c[2].metric("Pending", inr(total - rec))
    if rec > total: st.warning("Payments exceed invoice total")
    if st.button("Create sale", type="primary", disabled=not items or rec > total):
        try:
            r = write("createSale", branch_id=br, customer_id=cust, items=items, payments=pays, date=str(date.today()))
            st.success(f"{r['sale_id']} created — {r['status']}, pending {inr(r['pending'])}"); st.rerun()
        except ApiError as e:
            st.error(str(e))


def p_ledger():
    st.title("Sales Ledger")
    with st.expander("➕ New Sale"): new_sale_form()
    c = st.columns([2, 2, 3]); b = branch_filter("led_b") if ADMIN else None
    stt = c[1].selectbox("Status", ["", "PAID", "PARTIAL", "PENDING"]); q = c[2].text_input("Search TX / branch / customer / GSTIN")
    rows = get("ledger", branch_id=b, status=stt, q=q) if ADMIN else get("ledger", status=stt, q=q)
    d = fmt_ledger(rows); st.dataframe(d, use_container_width=True, hide_index=True)
    if not d.empty: st.download_button("CSV Export", d.to_csv(index=False), "ledger.csv")


def p_split():
    st.title("Split Payments")
    open_ = [r for r in get("ledger") if float(r["pending"]) > 0]
    with st.expander("Split calculator"):
        a = st.number_input("Amount (₹)", 0.0, value=100000.0); n = st.slider("Tranches", 2, 12, 3)
        st.write([inr(a / n)] * n)
    if not open_:
        st.success("No pending invoices."); return
    sid = st.selectbox("Invoice with balance", [r["sale_id"] for r in open_],
                       format_func=lambda x: (lambda r: f"{x} · {r['customer']} · pending {inr(r['pending'])}")(next(r for r in open_ if r["sale_id"] == x)))
    s = next(r for r in open_ if r["sale_id"] == sid)
    c = st.columns(3); c[0].metric("Invoiced", inr(s["total"])); c[1].metric("Received", inr(s["received"])); c[2].metric("Pending", inr(s["pending"]))
    st.dataframe(df(get("list", entity="Payments", sale_id=sid), ["payment_id", "date", "mode", "amount", "reference", "created_by"]), hide_index=True, use_container_width=True)
    with st.form("addpay"):
        m = st.selectbox("Mode", MODES); amt = st.number_input("Amount (₹)", 0.0, float(s["pending"]), float(s["pending"])); ref = st.text_input("Reference")
        if st.form_submit_button("Record payment", type="primary"):
            try:
                r = write("addPayment", sale_id=sid, mode=m, amount=amt, reference=ref, date=str(date.today()))
                st.success(f"Recorded. Status: {r['status']}, pending {inr(r['pending'])}"); st.rerun()
            except ApiError as e:
                st.error(str(e))


def p_tracking():
    st.title("Payment Tracking")
    rows = [r for r in get("ledger") if float(r["pending"]) > 0]
    d = fmt_ledger(rows)
    if d.empty: st.success("Nothing outstanding."); return
    d["Age (days)"] = [r["age_days"] for r in rows]
    st.metric("Total outstanding", inr(sum(float(r["pending"]) for r in rows)))
    st.dataframe(d.sort_values("Age (days)", ascending=False), use_container_width=True, hide_index=True)


def p_financial():
    st.title("Financial Tracking")
    b = branch_filter("fin_b"); f = get("financial", branch_id=b) if b else get("financial")
    c = st.columns(3); c[0].metric("Invoiced", inr(f["invoiced"])); c[1].metric("Received", inr(f["received"])); c[2].metric("Pending", inr(f["pending"]))
    l, r = st.columns(2)
    if f["by_mode"]: l.plotly_chart(px.pie(names=list(f["by_mode"]), values=list(f["by_mode"].values()), title="Received by mode", hole=.5), use_container_width=True)
    r.plotly_chart(px.bar(x=list(f["aging"]), y=list(f["aging"].values()), title="Receivables aging (days)", labels={"x": "", "y": "₹"}), use_container_width=True)
    if f["by_month"]: st.plotly_chart(px.bar(x=list(f["by_month"]), y=list(f["by_month"].values()), title="Collections by month", labels={"x": "", "y": "₹"}), use_container_width=True)


def p_analytics():
    st.title("Sales Analytics")
    b = branch_filter("an_b"); a = get("analytics", branch_id=b) if b else get("analytics")
    l, r = st.columns(2)
    if a["top_products"]: l.plotly_chart(px.bar(df(a["top_products"]), x="value", y="name", orientation="h", title="Top products"), use_container_width=True)
    if a["top_customers"]: r.plotly_chart(px.bar(df(a["top_customers"]), x="value", y="name", orientation="h", title="Top customers"), use_container_width=True)
    if a["branch_monthly"]: st.plotly_chart(px.line(df(a["branch_monthly"]).sort_values("month"), x="month", y="revenue", color="branch", markers=True, title="Monthly revenue by branch"), use_container_width=True)


def p_reports():
    st.title("Audit Reports")
    tabs = st.tabs(["Business reports"] + (["Audit log"] if ADMIN else []))
    with tabs[0]:
        t = st.selectbox("Report", ["sales", "outstanding", "collections", "stock", "branch_summary"])
        c = st.columns(3); fr = c[0].date_input("From", date(date.today().year if date.today().month > 3 else date.today().year - 1, 4, 1)); to = c[1].date_input("To", date.today())
        b = branch_filter("rep_b") if ADMIN else None
        d = df(get("report", type=t, **{"from": str(fr), "to": str(to)}, **({"branch_id": b} if b else {})))
        st.dataframe(d, use_container_width=True, hide_index=True)
        if not d.empty: st.download_button("Download CSV", d.to_csv(index=False), f"{t}.csv")
    if ADMIN:
        with tabs[1]:
            st.dataframe(df(get("audit", limit=500)), use_container_width=True, hide_index=True)


def p_users():
    st.title("Users & Roles")
    st.dataframe(df(get("users")), use_container_width=True, hide_index=True)
    with st.form("user"):
        un = st.text_input("Username (existing = update)"); pw = st.text_input("Password (blank = keep)", type="password")
        role = st.selectbox("Role", ["user", "admin"]); br = st.selectbox("Branch (users)", list(BR), format_func=BR.get) if BR else ""
        act = st.checkbox("Active", True)
        if st.form_submit_button("Save user", type="primary"):
            try:
                write("saveUser", username=un, password=pw, role=role, branch_id=br, active=act); st.success("Saved"); st.rerun()
            except ApiError as e:
                st.error(str(e))


PAGE_FN = {"Dashboard": p_dashboard, "Branches": p_branches, "Customers": p_customers, "Products": p_products, "Sales Ledger": p_ledger,
           "Split Payments": p_split, "Payment Tracking": p_tracking, "Financial Tracking": p_financial, "Sales Analytics": p_analytics,
           "Audit Reports": p_reports, "Users & Roles": p_users}
guard(PAGE_FN[page])