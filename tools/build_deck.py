"""Pitch deck PDF from the video slides (voters can open it from the vote card)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs" / "video"
slides = sorted((OUT / "slides").glob("*.png"))
with PdfPages(OUT / "deck.pdf") as pdf:
    for p in slides:
        img = mpimg.imread(p)
        fig = plt.figure(figsize=(12.8, 7.2), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.imshow(img)
        ax.axis("off")
        pdf.savefig(fig)
        plt.close(fig)
print(f"{len(slides)} pages -> {OUT / 'deck.pdf'}")
