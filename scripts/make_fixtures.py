#!/usr/bin/env python3
"""Generate the reproducible sample lecture PDF used by tests and the demo.

Run with:  python scripts/make_fixtures.py
Requires the ``dev`` extra (reportlab). The generated PDF is committed so the
test suite does not need reportlab installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover
    sys.exit("reportlab is required: uv pip install -e 'services/api[dev]'")

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "fixtures" / "sample-lecture-cnn.pdf"

#: (heading, body lines, equation or None)
SLIDES: list[tuple[str, list[str], str | None]] = [
    (
        "Convolutional Neural Networks",
        [
            "CS3410 Deep Learning - Lecture 7",
            "In this lecture we cover convolution, padding, stride, pooling and",
            "how gradients flow backwards through a convolutional layer.",
        ],
        None,
    ),
    (
        "Why not fully connected layers",
        [
            "A fully connected layer on a 224 by 224 colour image needs over 150000",
            "weights for a single unit, which is wasteful and overfits quickly.",
            "Images have local structure: nearby pixels are related, and a useful",
            "feature detector is useful wherever it appears in the image.",
            "Convolution exploits both properties through local connectivity and",
            "weight sharing, so the same kernel is applied at every position.",
        ],
        None,
    ),
    (
        "The convolution operation",
        [
            "A convolutional layer slides a small kernel over the input and computes",
            "a weighted sum at each position. The kernel weights are learned.",
            "For a two dimensional input the discrete convolution is written as:",
        ],
        "y[i,j] = sum_m sum_n x[i+m, j+n] * w[m,n] + b",
    ),
    (
        "Padding",
        [
            "Padding adds a border of extra values, usually zeros, around the input",
            "before the kernel is applied.",
            "Padding is necessary for two reasons. First, without padding the output",
            "is smaller than the input, so a deep stack of convolutions shrinks the",
            "feature map until no spatial information remains. Second, pixels at the",
            "edge of the image are covered by fewer kernel positions than pixels in",
            "the centre, so edge information is under represented.",
            "Same padding keeps the output the same size as the input. Valid padding",
            "applies no padding at all and the output is smaller.",
        ],
        None,
    ),
    (
        "Stride",
        [
            "Stride is the step size the kernel moves between applications.",
            "A stride of one moves the kernel by a single position and produces the",
            "densest output. A stride of two halves the spatial resolution and is a",
            "cheap alternative to pooling.",
            "The output size for input size W, kernel size K, padding P and stride S",
            "is given by the following expression:",
        ],
        "W_out = floor((W - K + 2P) / S) + 1",
    ),
    (
        "Pooling",
        [
            "Pooling reduces the spatial size of a feature map by summarising small",
            "neighbourhoods. Max pooling takes the largest value in each window and",
            "average pooling takes the mean.",
            "Pooling gives a small amount of translation invariance, because moving",
            "the input by one pixel often does not change which value is the maximum.",
            "Max pooling has no learned parameters, so it adds no weights.",
        ],
        None,
    ),
    (
        "Backpropagation through convolution",
        [
            "The gradient with respect to the kernel weights is itself a convolution",
            "between the input and the gradient arriving from the next layer.",
            "Because weights are shared across all positions, the gradient for one",
            "weight is the sum of contributions from every position where it was used.",
            "The gradient with respect to the input is a convolution of the incoming",
            "gradient with the kernel rotated by 180 degrees, often called a",
            "transposed convolution.",
        ],
        "dL/dw[m,n] = sum_i sum_j dL/dy[i,j] * x[i+m, j+n]",
    ),
    (
        "Receptive field",
        [
            "The receptive field of a unit is the region of the original input that",
            "can influence its value.",
            "Stacking convolutions grows the receptive field linearly, while adding",
            "stride or pooling grows it multiplicatively. This is why deep networks",
            "with small three by three kernels can still see large structures.",
        ],
        None,
    ),
    (
        "Summary",
        [
            "Convolution gives local connectivity and weight sharing.",
            "Padding preserves spatial size and treats edge pixels fairly.",
            "Stride controls how far the kernel moves and reduces resolution.",
            "Pooling summarises neighbourhoods and adds translation invariance.",
            "Gradients flow back through convolution as another convolution.",
        ],
        None,
    ),
]


def build() -> Path:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    width, height = A4
    pdf = canvas.Canvas(str(TARGET), pagesize=A4)
    pdf.setTitle("Convolutional Neural Networks - Lecture 7")
    pdf.setAuthor("StudyPilot fixtures")

    for heading, body, equation in SLIDES:
        # Heading: larger and bold, so heading detection has something real to find.
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(25 * mm, height - 30 * mm, heading)

        pdf.setFont("Helvetica", 12)
        y = height - 48 * mm
        for line in body:
            pdf.drawString(25 * mm, y, line)
            y -= 7.5 * mm

        if equation:
            y -= 4 * mm
            pdf.setFont("Courier-Bold", 13)
            pdf.drawString(30 * mm, y, equation)

        pdf.setFont("Helvetica", 9)
        pdf.drawString(
            width - 40 * mm,
            15 * mm,
            f"Slide {SLIDES.index((heading, body, equation)) + 1}",
        )
        pdf.showPage()

    pdf.save()
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes, {len(SLIDES)} pages)")
