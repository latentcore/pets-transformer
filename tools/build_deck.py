"""Pitch deck PDF (voters can open it from the vote card): the same slides as the video, one per page.

    python tools/build_video.py && python tools/build_deck.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs" / "video"


def main() -> None:
    pages = sorted((OUT / "slides").glob("*.png"), key=lambda p: p.stem)
    with PdfPages(OUT / "deck.pdf") as pdf:
        for p in pages:
            img = mpimg.imread(p)
            fig = plt.figure(figsize=(12.8, 7.2), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(img)
            ax.axis("off")
            pdf.savefig(fig)
            plt.close(fig)
    print(f"{len(pages)} pages -> {OUT / 'deck.pdf'}: {[p.stem for p in pages]}")


if __name__ == "__main__":
    main()
