# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drawing the CMS-1500 layout: boxes, labels and the service-line table.

Geometry only. What goes in each box is decided in :mod:`app.claims.cms1500`
(:func:`~app.claims.cms1500.cms1500_fields`); this module takes those
strings and puts them on a letter page with the standard Helvetica faces,
so nothing here depends on the claim or on a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from reportlab.lib.pagesizes import letter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from reportlab.pdfgen.canvas import Canvas

    from .cms1500 import Cms1500Fields, ServiceLineFields

#: Box 21 has twelve diagnosis slots, lettered A through L.
DX_LETTERS = "ABCDEFGHIJKL"

#: Box 24 has six service-line rows on the form.
SERVICE_LINE_ROWS = 6

_FONT = "Helvetica"
_BOLD = "Helvetica-Bold"
_LABEL_SIZE = 5.5
_VALUE_SIZE = 8.5
_TITLE_SIZE = 11

_PAGE_W, _PAGE_H = letter
_MARGIN = 24.0
_CONTENT_W = _PAGE_W - 2 * _MARGIN
_ROW_H = 30.0


# ---------------------------------------------------------------------------


def draw_form(canvas: Canvas, fields: Cms1500Fields) -> None:
    """The whole page: title, parties, diagnoses, service lines, totals, signature."""
    canvas.setLineWidth(0.5)
    _draw_title(canvas)
    y = _PAGE_H - _MARGIN - 16
    y = _draw_parties(canvas, y, fields)
    y = _draw_diagnoses(canvas, y, fields.box_21_diagnoses)
    y = _draw_service_lines(canvas, y, fields.box_24_lines)
    y = _draw_totals(canvas, y, fields)
    _draw_signature(canvas, y, fields)


def _draw_title(canvas: Canvas) -> None:
    canvas.setFont(_BOLD, _TITLE_SIZE)
    canvas.drawString(_MARGIN, _PAGE_H - _MARGIN - 4, "HEALTH INSURANCE CLAIM FORM")
    canvas.setFont(_FONT, _LABEL_SIZE + 1)
    canvas.drawRightString(
        _PAGE_W - _MARGIN,
        _PAGE_H - _MARGIN - 4,
        "CMS-1500 layout for transcription by the practice's biller. Not the official form.",
    )


def _draw_parties(canvas: Canvas, y: float, fields: Cms1500Fields) -> float:
    """Boxes 1a through 11c: who is insured, who was seen, under what plan."""
    half = _CONTENT_W / 2
    third = _CONTENT_W / 3

    y -= _ROW_H
    _cell(canvas, "1a", "INSURED'S I.D. NUMBER", _MARGIN, y, _CONTENT_W, [fields.box_1a_insured_id])

    y -= _ROW_H
    birth = f"{fields.box_3_birth_date}    SEX {fields.box_3_sex}"
    _cell(
        canvas, "2", "PATIENT'S NAME (Last, First)", _MARGIN, y, third, [fields.box_2_patient_name]
    )
    _cell(
        canvas, "3", "PATIENT'S BIRTH DATE (MM DD YYYY) / SEX", _MARGIN + third, y, third, [birth]
    )
    _cell(
        canvas,
        "4",
        "INSURED'S NAME (Last, First)",
        _MARGIN + 2 * third,
        y,
        third,
        [fields.box_4_insured_name],
    )

    address_h = _ROW_H * 1.8
    y -= address_h
    _cell(
        canvas,
        "5",
        "PATIENT'S ADDRESS, CITY STATE ZIP, PHONE",
        _MARGIN,
        y,
        third,
        fields.box_5_patient_address,
        h=address_h,
    )
    _cell(
        canvas,
        "6",
        "PATIENT RELATIONSHIP TO INSURED",
        _MARGIN + third,
        y,
        third,
        [fields.box_6_relationship],
        h=address_h,
    )
    _cell(
        canvas,
        "7",
        "INSURED'S ADDRESS, CITY STATE ZIP",
        _MARGIN + 2 * third,
        y,
        third,
        fields.box_7_insured_address,
        h=address_h,
    )

    y -= _ROW_H
    _cell(
        canvas,
        "11",
        "INSURED'S POLICY GROUP OR FECA NUMBER",
        _MARGIN,
        y,
        half,
        [fields.box_11_group_number],
    )
    _cell(
        canvas,
        "11c",
        "INSURANCE PLAN NAME OR PROGRAM NAME",
        _MARGIN + half,
        y,
        half,
        [fields.box_11c_plan_name],
    )
    return y


def _draw_diagnoses(canvas: Canvas, y: float, diagnoses: Sequence[str]) -> float:
    """Box 21: three rows of four slots, lettered A through L."""
    dx_h = _ROW_H * 1.6
    y -= dx_h
    _box(
        canvas,
        _Box(
            "21",
            "DIAGNOSIS OR NATURE OF ILLNESS OR INJURY    ICD Ind. 0",
            _MARGIN,
            y,
            _CONTENT_W,
            dx_h,
        ),
    )
    slot_w = _CONTENT_W / 4
    canvas.setFont(_FONT, _VALUE_SIZE)
    for index, letter_ in enumerate(DX_LETTERS):
        code = diagnoses[index] if index < len(diagnoses) else ""
        column, row = index % 4, index // 4
        canvas.drawString(
            _MARGIN + column * slot_w + 4, y + dx_h - 18 - row * 10, f"{letter_}. {code}"
        )
    return y


def _draw_totals(canvas: Canvas, y: float, fields: Cms1500Fields) -> float:
    """Boxes 25, 26, 28 and 29: tax id, account number, charge and paid."""
    quarter = _CONTENT_W / 4
    y -= _ROW_H
    tax_id = f"{fields.box_25_tax_id}  {fields.box_25_tax_id_type}"
    _cell(canvas, "25", "FEDERAL TAX I.D. NUMBER    SSN / EIN", _MARGIN, y, quarter, [tax_id])
    _cell(
        canvas,
        "26",
        "PATIENT'S ACCOUNT NO.",
        _MARGIN + quarter,
        y,
        quarter,
        [fields.box_26_account_number],
    )
    _cell(
        canvas,
        "28",
        "TOTAL CHARGE",
        _MARGIN + 2 * quarter,
        y,
        quarter,
        [f"$ {fields.box_28_total_charge}"],
    )
    _cell(
        canvas,
        "29",
        "AMOUNT PAID",
        _MARGIN + 3 * quarter,
        y,
        quarter,
        [f"$ {fields.box_29_amount_paid}"],
    )
    return y


def _draw_signature(canvas: Canvas, y: float, fields: Cms1500Fields) -> None:
    """Boxes 31, 33 and 33a: who signs, who bills, under which NPI."""
    half = _CONTENT_W / 2
    sig_h = _ROW_H * 2.4
    y -= sig_h
    signature = [fields.box_31_signature, f"DATE {fields.box_31_date}"]
    _cell(
        canvas,
        "31",
        "SIGNATURE OF PHYSICIAN OR SUPPLIER / DATE",
        _MARGIN,
        y,
        half,
        signature,
        h=sig_h,
    )
    _cell(
        canvas,
        "33",
        "BILLING PROVIDER INFO & PH #",
        _MARGIN + half,
        y,
        half,
        fields.box_33_billing_provider,
        h=sig_h,
    )
    canvas.setFont(_BOLD, _LABEL_SIZE)
    canvas.drawString(_MARGIN + half + 3, y + 4, "33a. NPI")
    canvas.setFont(_FONT, _VALUE_SIZE)
    canvas.drawString(_MARGIN + half + 36, y + 4, fields.box_33a_billing_npi)


_LINE_COLUMNS: tuple[tuple[str, str, float], ...] = (
    ("A", "DATE(S) OF SERVICE  From / To", 0.24),
    ("B", "POS", 0.06),
    ("D", "PROCEDURES (CPT/HCPCS) / MODIFIER", 0.22),
    ("E", "DX PTR", 0.08),
    ("F", "$ CHARGES", 0.12),
    ("G", "UNITS", 0.07),
    ("J", "RENDERING PROVIDER NPI", 0.21),
)


def _draw_service_lines(canvas: Canvas, y: float, lines: Sequence[ServiceLineFields]) -> float:
    header_h = 24.0
    row_h = 16.0
    table_h = header_h + row_h * SERVICE_LINE_ROWS
    y -= table_h
    _box(canvas, _Box("24", "SERVICE LINES", _MARGIN, y, _CONTENT_W, table_h))

    x = _MARGIN
    edges: list[float] = []
    canvas.setFont(_BOLD, _LABEL_SIZE)
    for letter_, label, share in _LINE_COLUMNS:
        edges.append(x)
        canvas.drawString(x + 3, y + table_h - 19, f"{letter_}. {label}")
        x += _CONTENT_W * share
    canvas.line(_MARGIN, y + table_h - header_h, _MARGIN + _CONTENT_W, y + table_h - header_h)
    for edge in edges[1:]:
        canvas.line(edge, y, edge, y + table_h - header_h)

    canvas.setFont(_FONT, _VALUE_SIZE)
    for row in range(SERVICE_LINE_ROWS):
        row_y = y + table_h - header_h - row_h * (row + 1)
        canvas.line(_MARGIN, row_y, _MARGIN + _CONTENT_W, row_y)
        if row >= len(lines):
            continue
        line = lines[row]
        cells = (
            f"{line.date_from}  -  {line.date_to}",
            line.place_of_service,
            f"{line.cpt}  {line.modifiers}".rstrip(),
            line.dx_pointer,
            line.charges,
            line.units,
            line.rendering_npi,
        )
        for edge, cell in zip(edges, cells, strict=True):
            canvas.drawString(edge + 3, row_y + 5, cell)
    return y


@dataclass(frozen=True)
class _Box:
    number: str
    label: str
    x: float
    y: float
    w: float
    h: float


def _box(canvas: Canvas, box: _Box) -> None:
    canvas.rect(box.x, box.y, box.w, box.h)
    canvas.setFont(_BOLD, _LABEL_SIZE)
    canvas.drawString(box.x + 3, box.y + box.h - 8, f"{box.number}. {box.label}")


def _cell(  # noqa: PLR0913 — a box's geometry plus what goes in it
    canvas: Canvas,
    number: str,
    label: str,
    x: float,
    y: float,
    w: float,
    lines: Sequence[str],
    *,
    h: float = _ROW_H,
) -> None:
    """A labelled box with ``lines`` printed under the label, top-aligned."""
    _box(canvas, _Box(number, label, x, y, w, h))
    canvas.setFont(_FONT, _VALUE_SIZE)
    for offset, text in enumerate(lines):
        canvas.drawString(x + 4, y + h - 20 - offset * 10, text)
