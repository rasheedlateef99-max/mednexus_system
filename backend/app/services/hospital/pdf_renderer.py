"""
PDF Renderer
------------------
Renders Jinja2 HTML templates to PDF bytes via WeasyPrint.

Templates live in backend/templates/. Each render function builds
a context dict matching what its template expects, then hands off
to WeasyPrint for the HTML→PDF conversion.
"""

import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR))


def _render_html(template_name: str, context: dict) -> str:
    template = _env.get_template(template_name)
    return template.render(**context)


def render_pdf(template_name: str, context: dict) -> bytes:
    """Renders a named template with the given context to PDF bytes."""
    html_string = _render_html(template_name, context)
    return HTML(string=html_string).write_pdf()


def render_invoice_pdf(invoice, items, payments, hospital_name: str, patient_name: str) -> bytes:
    context = {
        "invoice": invoice,
        "items": items,
        "payments": payments,
        "hospital_name": hospital_name,
        "patient_name": patient_name,
        "generated_at": datetime.utcnow(),
    }
    return render_pdf("invoice.html", context)


def render_prescription_pdf(prescription, items, hospital_name: str, patient_name: str, doctor_name: str) -> bytes:
    context = {
        "prescription": prescription,
        "items": items,
        "hospital_name": hospital_name,
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "generated_at": datetime.utcnow(),
    }
    return render_pdf("prescription.html", context)


def render_lab_report_pdf(lab_order, items, hospital_name: str, patient_name: str, doctor_name: str) -> bytes:
    context = {
        "lab_order": lab_order,
        "items": items,
        "hospital_name": hospital_name,
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "generated_at": datetime.utcnow(),
    }
    return render_pdf("lab_report.html", context)