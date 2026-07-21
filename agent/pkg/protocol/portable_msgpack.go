package protocol

// This file is the only MsgPack boundary in the protocol package. It encodes
// application task values stored in ValueRef.inline; control messages use
// generated Protobuf exclusively.

import (
	"bytes"
	"errors"
	"fmt"
	"math"

	"github.com/vmihailenco/msgpack/v5"
)

type portableSemanticError struct{ message string }

func (e *portableSemanticError) Error() string { return e.message }

func validatePortableValue(value interface{}) error {
	switch v := value.(type) {
	case nil, bool, string, []byte:
		return nil
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64:
		return nil
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return NewDecodeError(InvalidMessage, "portable float must be finite")
		}
		return nil
	case []interface{}:
		for _, item := range v {
			if err := validatePortableValue(item); err != nil {
				return err
			}
		}
		return nil
	case map[string]interface{}:
		for _, item := range v {
			if err := validatePortableValue(item); err != nil {
				return err
			}
		}
		return nil
	default:
		return NewDecodeError(InvalidMessage, fmt.Sprintf("unsupported portable value type %T", value))
	}
}

// EncodePortableValue uses deterministic key ordering, compact integers, and
// float64 encoding. No schema or control envelope is encoded as MsgPack.
func EncodePortableValue(value interface{}) ([]byte, error) {
	if err := validatePortableValue(value); err != nil {
		return nil, err
	}
	var output bytes.Buffer
	encoder := msgpack.NewEncoder(&output)
	encoder.SetSortMapKeys(true)
	encoder.UseCompactInts(true)
	encoder.UseCompactFloats(false)
	if err := encoder.Encode(value); err != nil {
		return nil, NewDecodeError(InvalidMessage, err.Error())
	}
	return output.Bytes(), nil
}

// DecodePortableValue rejects duplicate/non-string map keys, extension values,
// non-finite/float32 values, and trailing bytes after the single task value.
func DecodePortableValue(payload []byte) (interface{}, error) {
	reader := bytes.NewReader(payload)
	decoder := msgpack.NewDecoder(reader)
	decoder.SetMapDecoder(func(d *msgpack.Decoder) (interface{}, error) {
		length, err := d.DecodeMapLen()
		if err != nil {
			return nil, err
		}
		result := make(map[string]interface{}, length)
		for i := 0; i < length; i++ {
			key, err := d.DecodeString()
			if err != nil {
				return nil, &portableSemanticError{message: "portable map key must be UTF-8 string"}
			}
			if _, exists := result[key]; exists {
				return nil, &portableSemanticError{message: "duplicate portable map key"}
			}
			item, err := d.DecodeInterface()
			if err != nil {
				return nil, err
			}
			result[key] = item
		}
		return result, nil
	})
	value, err := decoder.DecodeInterface()
	if err != nil {
		var semantic *portableSemanticError
		if errors.As(err, &semantic) {
			return nil, NewDecodeError(InvalidMessage, semantic.Error())
		}
		return nil, NewDecodeError(MalformedPayload, err.Error())
	}
	if reader.Len() != 0 {
		_, trailingErr := decoder.DecodeInterface()
		if trailingErr == nil {
			return nil, NewDecodeError(InvalidMessage, "trailing portable value bytes")
		}
		var semantic *portableSemanticError
		if errors.As(trailingErr, &semantic) {
			return nil, NewDecodeError(InvalidMessage, semantic.Error())
		}
		return nil, NewDecodeError(MalformedPayload, trailingErr.Error())
	}
	if err := validatePortableValue(value); err != nil {
		return nil, err
	}
	return value, nil
}
