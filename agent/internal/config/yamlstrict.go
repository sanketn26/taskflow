package config

import (
	"bytes"
	"fmt"
	"io"

	"gopkg.in/yaml.v3"
)

// standardTags is the set of resolved tags a strict document may use.
// Anything else (custom application tags) is rejected.
var standardTags = map[string]bool{
	"!!str": true, "!!int": true, "!!bool": true, "!!float": true,
	"!!null": true, "!!map": true, "!!seq": true, "!!timestamp": true, "!!binary": true,
	"": true,
}

// loadYAMLStrict parses data into a generic map[string]interface{} /
// []interface{} / scalar tree, rejecting duplicate keys, YAML aliases,
// non-string map keys, custom tags, and multiple documents.
func loadYAMLStrict(data []byte) (interface{}, error) {
	dec := yaml.NewDecoder(bytes.NewReader(data))

	var root yaml.Node
	if err := dec.Decode(&root); err != nil {
		if err == io.EOF {
			return map[string]interface{}{}, nil
		}
		return nil, fmt.Errorf("parse config: %w", err)
	}

	var second yaml.Node
	if err := dec.Decode(&second); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("multiple YAML documents are not permitted")
		}
		return nil, fmt.Errorf("parse config: %w", err)
	}

	return nodeToValue(&root)
}

func nodeToValue(n *yaml.Node) (interface{}, error) {
	switch n.Kind {
	case yaml.DocumentNode:
		if len(n.Content) == 0 {
			return map[string]interface{}{}, nil
		}
		return nodeToValue(n.Content[0])

	case yaml.AliasNode:
		return nil, fmt.Errorf("YAML aliases are not permitted")

	case yaml.MappingNode:
		if !standardTags[n.Tag] {
			return nil, fmt.Errorf("custom tag %q is not permitted", n.Tag)
		}
		out := make(map[string]interface{}, len(n.Content)/2)
		for i := 0; i+1 < len(n.Content); i += 2 {
			keyNode := n.Content[i]
			valNode := n.Content[i+1]
			if keyNode.Kind == yaml.AliasNode || valNode.Kind == yaml.AliasNode {
				return nil, fmt.Errorf("YAML aliases are not permitted")
			}
			if keyNode.Kind != yaml.ScalarNode || keyNode.Tag != "!!str" {
				return nil, fmt.Errorf("map keys must be strings")
			}
			key := keyNode.Value
			if _, dup := out[key]; dup {
				return nil, fmt.Errorf("duplicate key %q", key)
			}
			v, err := nodeToValue(valNode)
			if err != nil {
				return nil, err
			}
			out[key] = v
		}
		return out, nil

	case yaml.SequenceNode:
		if !standardTags[n.Tag] {
			return nil, fmt.Errorf("custom tag %q is not permitted", n.Tag)
		}
		out := make([]interface{}, 0, len(n.Content))
		for _, c := range n.Content {
			v, err := nodeToValue(c)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil

	case yaml.ScalarNode:
		if !standardTags[n.Tag] {
			return nil, fmt.Errorf("custom tag %q is not permitted", n.Tag)
		}
		var v interface{}
		if err := n.Decode(&v); err != nil {
			return nil, err
		}
		// yaml.v3 decodes plain integers into int; normalize to int64 for
		// a single integer type downstream.
		if iv, ok := v.(int); ok {
			return int64(iv), nil
		}
		return v, nil

	default:
		return nil, fmt.Errorf("unsupported YAML node kind %v", n.Kind)
	}
}
