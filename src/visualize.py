"""
visualize.py — Suspicious-subgraph visualisation (interactive HTML + PNG).

Tries pyvis first (interactive, draggable, zoomable). If pyvis is not
available, falls back to a static HTML page embedding a matplotlib PNG —
the pipeline never fails just because of a visualisation dependency.

The subgraph shown is the neighbourhood (1 hop) of the top-ranked
suspicious accounts, capped at ~80 nodes so the HTML stays readable.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd


def suspicious_subgraph(graph: nx.DiGraph, ranked: pd.DataFrame,
                        ground_truth: pd.DataFrame, max_nodes: int = 80) -> nx.DiGraph:
    planted = set(ground_truth["account_id"])
    top = ranked.head(25)["account_id"].tolist()
    seeds = [a for a in top if a in graph] or list(planted)[:10]
    nodes: set[str] = set()
    for seed in seeds:
        nodes.add(seed)
        nodes.update(list(graph.successors(seed))[:10])
        nodes.update(list(graph.predecessors(seed))[:10])
        if len(nodes) >= max_nodes:
            break
    sub = graph.subgraph(sorted(nodes)).copy()
    for n in sub.nodes:
        sub.nodes[n]["planted"] = n in planted
        sub.nodes[n]["top_ranked"] = n in set(top)
    return sub


def make_png(sub: nx.DiGraph, out_path: Path) -> None:
    planted = {n for n, d in sub.nodes(data=True) if d.get("planted")}
    top = {n for n, d in sub.nodes(data=True) if d.get("top_ranked")}
    pos = nx.spring_layout(sub, seed=42, k=0.6)
    plt.figure(figsize=(12, 9))
    colours = ["#c0392b" if n in planted else ("#e67e22" if n in top else "#5d6d7e")
               for n in sub.nodes]
    sizes = [320 if n in planted else 140 for n in sub.nodes]
    nx.draw_networkx_nodes(sub, pos, node_color=colours, node_size=sizes, alpha=0.9)
    nx.draw_networkx_edges(sub, pos, alpha=0.35, arrows=True, arrowsize=10,
                           edge_color="#7f8c8d")
    labels = {n: n.replace("CTA-", "") for n in sub.nodes if n in planted or n in top}
    nx.draw_networkx_labels(sub, pos, labels, font_size=7)
    plt.title("AML-GraphAlert — Suspicious subgraph (red = planted typology, "
              "orange = top-ranked by risk score)", fontsize=11)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  wrote {out_path}")


def make_html(sub: nx.DiGraph, out_path: Path, png_path: Path) -> None:
    try:
        from pyvis.network import Network
    except ImportError:
        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>AML-GraphAlert — Suspicious subgraph</title></head><body>
<h1>AML-GraphAlert — Suspicious subgraph</h1>
<p>pyvis was not installed, so this is a static fallback. Red nodes = planted
typology accounts; orange = top-ranked by risk score.</p>
<img src="{png_path.name}" style="max-width:100%">
<p>Install pyvis (<code>pip install pyvis</code>) and re-run the pipeline for
the interactive version.</p></body></html>"""
        out_path.write_text(html, encoding="utf-8")
        print(f"  wrote {out_path} (static fallback — pyvis not installed)")
        return
    net = Network(height="750px", width="100%", directed=True, notebook=False,
                  bgcolor="#ffffff", font_color="#222222")
    net.barnes_hut()
    for n, d in sub.nodes(data=True):
        if d.get("planted"):
            colour = "#c0392b"
            title = f"{n} — planted typology (ground truth, synthetic)"
        elif d.get("top_ranked"):
            colour, title = "#e67e22", f"{n} — top-ranked by risk score"
        else:
            colour, title = "#5d6d7e", str(n)
        net.add_node(n, label=n, title=title, color=colour,
                     size=22 if d.get("planted") else 14)
    for u, v, d in sub.edges(data=True):
        edge_title = (f"MXN {d.get('weight', 0):,.0f} · "
                      f"{d.get('n', 0)} transfers")
        net.add_edge(u, v, title=edge_title,
                     value=float(d.get("weight", 1)) ** 0.25)
    net.set_options("""
    {"physics": {"barnesHut": {"gravitationalConstant": -8000, "springLength": 120}},
     "interaction": {"hover": true, "tooltipDelay": 100}}
    """)
    net.save_graph(str(out_path))
    # pyvis writes its JS assets to ./lib relative to the working directory;
    # move them next to the HTML file so the page is self-contained in outputs/.
    import shutil
    cwd_lib = Path.cwd() / "lib"
    target_lib = out_path.parent / "lib"
    if cwd_lib.exists() and cwd_lib.resolve() != target_lib.resolve():
        if target_lib.exists():
            shutil.rmtree(target_lib)
        shutil.move(str(cwd_lib), str(target_lib))
    print(f"  wrote {out_path} (interactive, pyvis)")


def make_visualisations(transactions: pd.DataFrame, graph: nx.DiGraph,
                        ranked: pd.DataFrame, ground_truth: pd.DataFrame,
                        out_dir: Path, cycles: pd.DataFrame) -> None:
    sub = suspicious_subgraph(graph, ranked, ground_truth)
    png_path = out_dir / "suspicious_subgraph.png"
    html_path = out_dir / "suspicious_subgraph.html"
    make_png(sub, png_path)
    make_html(sub, html_path, png_path)
    print(f"Subgraph: {sub.number_of_nodes()} nodes, {sub.number_of_edges()} edges")
