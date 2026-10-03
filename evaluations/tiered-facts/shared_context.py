"""Offline prototype: lossless shared context; not enabled in the pipeline."""
import json
from collections import Counter

MARKER='shared_context_ref'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def pack(value):
    """Intern identical large JSON nodes, never summarize or clip evidence."""
    counts=Counter()
    def count(node):
        if isinstance(node,dict) and MARKER in node:
            raise ValueError('Reserved reference key in original input')
        key=canonical(node)
        if len(key)>=128:counts[key]+=1
        for child in (node.values() if isinstance(node,dict) else node if isinstance(node,list) else []):count(child)
    count(value)
    definitions={};ids={}
    def encode(node,definition=False):
        key=canonical(node)
        if not definition and counts[key]>1:
            if key not in ids:
                ids[key]='d'+str(len(ids)+1)
                definitions[ids[key]]=encode(node,True)
            return {MARKER:ids[key]}
        if isinstance(node,dict):return {k:encode(v) for k,v in node.items()}
        if isinstance(node,list):return [encode(v) for v in node]
        return node
    root=encode(value)
    # Interned parents can make a child definition redundant. Inline nodes used
    # once; keep exact values and ordering, including excerpt boundaries.
    while True:
        uses=Counter()
        def scan(node):
            if isinstance(node,dict) and set(node)=={MARKER}:uses[node[MARKER]]+=1
            else:
                for child in (node.values() if isinstance(node,dict) else node if isinstance(node,list) else []):scan(child)
        scan(root)
        for node in definitions.values():scan(node)
        singles={k for k in definitions if uses[k]<=1}
        if not singles:break
        def inline(node):
            if isinstance(node,dict) and set(node)=={MARKER} and node[MARKER] in singles:return inline(definitions[node[MARKER]])
            if isinstance(node,dict):return {k:inline(v) for k,v in node.items()}
            if isinstance(node,list):return [inline(v) for v in node]
            return node
        root=inline(root)
        definitions={k:inline(v) for k,v in definitions.items() if k not in singles}
    return {'definitions':definitions,'requests':root}


def unpack(bundle):
    def decode(node,seen=frozenset()):
        if isinstance(node,dict) and set(node)=={MARKER}:
            key=node[MARKER]
            if key in seen:raise ValueError('Cyclic reference')
            return decode(bundle['definitions'][key],seen|{key})
        if isinstance(node,dict):return {k:decode(v,seen) for k,v in node.items()}
        if isinstance(node,list):return [decode(v,seen) for v in node]
        return node
    return decode(bundle['requests'])
