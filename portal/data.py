APPLICATIONS = {
    "LN-1042": {
        "status": "Waiting for Documents",
        "applicant": "Jane Smith",
        "loan_type": "Personal Loan",
        "amount": 25000,
        "missing_docs": ["Proof of Income", "Bank Statements"],
        "next_step": "Applicant needs to upload a recent payslip and last 3 months bank statements",
        "submitted": "2024-11-15",
        "officer": "Mike Johnson",
    },
    "LN-2099": {
        "status": "Under Review",
        "applicant": "Robert Chen",
        "loan_type": "Auto Loan",
        "amount": 42000,
        "missing_docs": [],
        "next_step": "Pending credit committee review scheduled for next week",
        "submitted": "2024-12-01",
        "officer": "Sarah Williams",
    },
    "LN-3201": {
        "status": "Approved - Pending Closing",
        "applicant": "Maria Garcia",
        "loan_type": "Mortgage",
        "amount": 350000,
        "missing_docs": ["Title Insurance"],
        "next_step": "Awaiting title insurance documentation before closing",
        "submitted": "2024-10-20",
        "officer": "Mike Johnson",
    },
    "LN-4455": {
        "status": "Denied",
        "applicant": "Tom Baker",
        "loan_type": "Business Loan",
        "amount": 150000,
        "missing_docs": [],
        "next_step": "Application denied due to insufficient collateral. Applicant may reapply in 90 days.",
        "submitted": "2024-11-28",
        "officer": "Sarah Williams",
    },
    "LN-5500": {
        "status": "Waiting for Documents",
        "applicant": "Alice Wong",
        "loan_type": "Home Equity",
        "amount": 75000,
        "missing_docs": ["Property Appraisal", "Proof of Insurance"],
        "next_step": "Applicant must provide current property appraisal and homeowner's insurance certificate",
        "submitted": "2024-12-10",
        "officer": "Mike Johnson",
    },
}


def get_application(app_id: str) -> dict | None:
    app = APPLICATIONS.get(app_id)
    if app is None:
        return None
    return {"id": app_id, **app}


def search_applications(query: str) -> list[dict]:
    q = query.casefold()
    results = []
    for app_id, app in APPLICATIONS.items():
        if q in app_id.casefold() or q in app["applicant"].casefold():
            results.append({"id": app_id, **app})
    return results
