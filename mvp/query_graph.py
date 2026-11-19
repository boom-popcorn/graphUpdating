"""Query persistent graphs through the MCP client."""
import argparse
import json
from .io import read_json
from .jena import JenaGraphStore, ROOT
from .mcp_client import MCPClientToolbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--vessel", default="V-017")
    parser.add_argument("--subgraph", help="Return a one-hop subgraph instead of vessel states")
    args = parser.parse_args()
    graph = JenaGraphStore(read_json(ROOT / "data" / "initial_graph.json"), args.dataset_dir)
    tools = MCPClientToolbox(graph)
    result = tools.call("getSubgraph", {"entityId": args.subgraph}) if args.subgraph else tools.call("queryState", {"vesselId": args.vessel})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
