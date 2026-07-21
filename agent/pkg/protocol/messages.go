package protocol

// Control-plane schemas are generated from proto/taskwire/v1/control.proto.
// This file provides the small framing dispatch and semantic validation layer;
// MsgPack is not used here.

import (
	"fmt"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/proto"
)

type ObjectRef = pb.ObjectRef
type ValueRef = pb.ValueRef
type Hello = pb.Hello
type PullRequest = pb.PullRequest
type TaskCapability = pb.TaskCapability
type TaskRegistration = pb.TaskRegistration
type TaskQuery = pb.TaskQuery
type TaskSnapshotEntry = pb.TaskSnapshotEntry
type TaskSnapshot = pb.TaskSnapshot
type TaskEnvelope = pb.TaskEnvelope
type LeasedTask = pb.LeasedTask
type Completion = pb.Completion
type ForwardedTask = pb.ForwardedTask
type ForwardedCompletion = pb.ForwardedCompletion
type Failure = pb.Failure
type ResultNotification = pb.ResultNotification
type StatusSnapshot = pb.StatusSnapshot
type Ack = pb.Ack
type Error = pb.Error
type HeartbeatRequest = pb.HeartbeatRequest
type CancelRequest = pb.CancelRequest
type ResumeResultsRequest = pb.ResumeResultsRequest
type ObjectPutRequest = pb.ObjectPutRequest
type ObjectGetRequest = pb.ObjectGetRequest
type ObjectChunk = pb.ObjectChunk
type StealRequest = pb.StealRequest
type StatusRequest = pb.StatusRequest
type ControlMessage = pb.ControlMessage

func requireID(value []byte, field string) error {
	if len(value) != 16 {
		return NewDecodeError(InvalidMessage, field+" must be 16 bytes")
	}
	return nil
}

func requireNonEmpty(value, field string) error {
	if value == "" {
		return NewDecodeError(InvalidMessage, field+" must not be empty")
	}
	return nil
}

func uniqueStrings(values []string, field string) error {
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if value == "" || seen[value] {
			return NewDecodeError(InvalidMessage, field+" must contain unique non-empty strings")
		}
		seen[value] = true
	}
	return nil
}

func validateOutcome(outcome interface{}) error {
	switch value := outcome.(type) {
	case *pb.Completion_Result:
		return validateMessage(value.Result)
	case *pb.Completion_Failure:
		return validateMessage(value.Failure)
	case *pb.ForwardedCompletion_Result:
		return validateMessage(value.Result)
	case *pb.ForwardedCompletion_Failure:
		return validateMessage(value.Failure)
	case *pb.ResultNotification_Result:
		return validateMessage(value.Result)
	case *pb.ResultNotification_Failure:
		return validateMessage(value.Failure)
	default:
		return NewDecodeError(InvalidMessage, "result/failure outcome is required")
	}
}

var ackKindFields = map[string]map[string]bool{
	"hello": {}, "submit": {"task_id": true}, "forward": {"task_id": true, "transfer_id": true},
	"heartbeat": {"lease_id": true}, "complete": {"lease_id": true},
	"cancel": {"task_id": true, "cancelled": true},
	"result": {"owner_id": true, "task_id": true, "cursor": true}, "empty_pull": {},
	"object_put": {"transfer_id": true, "object": true}, "object_get": {"transfer_id": true},
	"resume":         {"owner_id": true, "next_cursor": true, "more": true},
	"steal":          {"transfer_id": true, "accepted": true},
	"register_tasks": {"worker_id": true, "generation": true, "accepted": true},
}

func validateAck(value *pb.Ack) error {
	expected, ok := ackKindFields[value.Kind]
	if !ok {
		return NewDecodeError(InvalidMessage, "unknown ACK kind")
	}
	present := map[string]bool{}
	if len(value.TaskId) != 0 {
		present["task_id"] = true
	}
	if len(value.TransferId) != 0 {
		present["transfer_id"] = true
	}
	if len(value.LeaseId) != 0 {
		present["lease_id"] = true
	}
	if len(value.OwnerId) != 0 {
		present["owner_id"] = true
	}
	if value.Cursor != nil {
		present["cursor"] = true
	}
	if value.Cancelled != nil {
		present["cancelled"] = true
	}
	if value.Object != nil {
		present["object"] = true
	}
	if value.NextCursor != nil {
		present["next_cursor"] = true
	}
	if value.More != nil {
		present["more"] = true
	}
	if value.Accepted != nil {
		present["accepted"] = true
	}
	if value.WorkerId != "" {
		present["worker_id"] = true
	}
	if value.Generation != nil {
		present["generation"] = true
	}
	if len(present) != len(expected) {
		return NewDecodeError(InvalidMessage, "ACK fields do not match kind")
	}
	for field := range expected {
		if !present[field] {
			return NewDecodeError(InvalidMessage, "ACK fields do not match kind")
		}
	}
	for field := range expected {
		switch field {
		case "task_id":
			if err := requireID(value.TaskId, field); err != nil {
				return err
			}
		case "transfer_id":
			if err := requireID(value.TransferId, field); err != nil {
				return err
			}
		case "lease_id":
			if err := requireID(value.LeaseId, field); err != nil {
				return err
			}
		case "owner_id":
			if err := requireID(value.OwnerId, field); err != nil {
				return err
			}
		case "object":
			if err := validateMessage(value.Object); err != nil {
				return err
			}
		case "worker_id":
			if err := requireNonEmpty(value.WorkerId, field); err != nil {
				return err
			}
		}
	}
	if value.Kind == "register_tasks" && value.GetGeneration() == 0 {
		return NewDecodeError(InvalidMessage, "generation must be positive")
	}
	return nil
}

func validateMessage(message proto.Message) error {
	switch value := message.(type) {
	case *pb.Hello:
		switch value.Role {
		case "runtime":
			if err := requireID(value.OwnerId, "owner_id"); err != nil {
				return err
			}
			if value.WorkerId != "" || value.Runtime != "" || value.RuntimeVersion != "" || value.SdkVersion != "" || len(value.Codecs) != 0 {
				return NewDecodeError(InvalidMessage, "runtime HELLO contains worker fields")
			}
		case "worker":
			if len(value.OwnerId) != 0 || value.WorkerId == "" || value.RuntimeVersion == "" || value.SdkVersion == "" || len(value.Codecs) == 0 {
				return NewDecodeError(InvalidMessage, "worker HELLO identity is incomplete")
			}
			if value.Runtime != "python" && value.Runtime != "nodejs" && value.Runtime != "go" {
				return NewDecodeError(InvalidMessage, "invalid worker runtime")
			}
			if err := uniqueStrings(value.Codecs, "codecs"); err != nil {
				return err
			}
		case "admin":
			if len(value.OwnerId) != 0 || value.WorkerId != "" || value.Runtime != "" || value.RuntimeVersion != "" || value.SdkVersion != "" || len(value.Codecs) != 0 {
				return NewDecodeError(InvalidMessage, "admin HELLO contains identity fields")
			}
		default:
			return NewDecodeError(InvalidMessage, "invalid HELLO role")
		}
	case *pb.ObjectRef:
		if value.Store == "" || value.Key == "" || value.Codec == "" || len(value.Sha256) != 32 {
			return NewDecodeError(InvalidMessage, "invalid ObjectRef")
		}
	case *pb.ValueRef:
		switch location := value.Location.(type) {
		case *pb.ValueRef_Inline:
			if value.Codec == "" {
				return NewDecodeError(InvalidMessage, "inline ValueRef requires codec")
			}
		case *pb.ValueRef_Object:
			if value.Codec != "" {
				return NewDecodeError(InvalidMessage, "object ValueRef cannot declare codec")
			}
			return validateMessage(location.Object)
		default:
			return NewDecodeError(InvalidMessage, "ValueRef location is required")
		}
	case *pb.TaskRegistration:
		if value.WorkerId == "" || value.Generation == 0 {
			return NewDecodeError(InvalidMessage, "invalid task registration")
		}
		seen := make(map[string]bool)
		for _, task := range value.Tasks {
			if task == nil {
				return NewDecodeError(InvalidMessage, "nil task capability")
			}
			key := task.TaskName + "\x00" + task.TaskVersion
			if task.TaskName == "" || task.TaskVersion == "" || (task.Invocation != "value" && task.Invocation != "python_args") || len(task.Codecs) == 0 || seen[key] {
				return NewDecodeError(InvalidMessage, "invalid task capability")
			}
			if err := uniqueStrings(task.Codecs, "task codecs"); err != nil {
				return err
			}
			seen[key] = true
		}
	case *pb.PullRequest:
		if value.WorkerId == "" || value.CapabilityGeneration == 0 {
			return NewDecodeError(InvalidMessage, "invalid pull request")
		}
	case *pb.TaskQuery:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if len(value.TaskIds) == 0 {
			return NewDecodeError(InvalidMessage, "task_ids must not be empty")
		}
		for _, id := range value.TaskIds {
			if err := requireID(id, "task_id"); err != nil {
				return err
			}
		}
	case *pb.TaskSnapshot:
		for _, task := range value.Tasks {
			if task == nil {
				return NewDecodeError(InvalidMessage, "nil task snapshot entry")
			}
			if err := validateMessage(task); err != nil {
				return err
			}
		}
	case *pb.TaskSnapshotEntry:
		if err := requireID(value.TaskId, "task_id"); err != nil {
			return err
		}
		switch value.State {
		case "queued", "leased", "unknown":
			if value.Cursor != nil || value.Outcome != nil {
				return NewDecodeError(InvalidMessage, "nonterminal task snapshot has terminal fields")
			}
		case "succeeded":
			result, ok := value.Outcome.(*pb.TaskSnapshotEntry_Result)
			if value.Cursor == nil || !ok {
				return NewDecodeError(InvalidMessage, "succeeded snapshot requires result and cursor")
			}
			return validateMessage(result.Result)
		case "failed", "cancelled", "dead_lettered":
			failure, ok := value.Outcome.(*pb.TaskSnapshotEntry_Failure)
			if value.Cursor == nil || !ok {
				return NewDecodeError(InvalidMessage, "failed snapshot requires failure and cursor")
			}
			return validateMessage(failure.Failure)
		default:
			return NewDecodeError(InvalidMessage, "invalid task snapshot state")
		}
	case *pb.TaskEnvelope:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if value.TaskName == "" || value.TaskVersion == "" || (value.Invocation != "value" && value.Invocation != "python_args") || value.Input == nil {
			return NewDecodeError(InvalidMessage, "invalid task envelope")
		}
		return validateMessage(value.Input)
	case *pb.LeasedTask:
		if value.Task == nil || value.TtlMs == 0 || value.Attempt == 0 {
			return NewDecodeError(InvalidMessage, "invalid leased task")
		}
		if err := requireID(value.LeaseId, "lease_id"); err != nil {
			return err
		}
		return validateMessage(value.Task)
	case *pb.Completion:
		if err := requireID(value.LeaseId, "lease_id"); err != nil {
			return err
		}
		return validateOutcome(value.Outcome)
	case *pb.ForwardedTask:
		if err := requireID(value.TransferId, "transfer_id"); err != nil {
			return err
		}
		if value.OriginNode == "" || value.Task == nil {
			return NewDecodeError(InvalidMessage, "invalid forwarded task")
		}
		return validateMessage(value.Task)
	case *pb.ForwardedCompletion:
		if err := requireID(value.TransferId, "transfer_id"); err != nil {
			return err
		}
		if value.RemoteNode == "" || value.RemoteAttempt == 0 {
			return NewDecodeError(InvalidMessage, "invalid forwarded completion")
		}
		return validateOutcome(value.Outcome)
	case *pb.Failure:
		if value.Code == "" || value.Message == "" {
			return NewDecodeError(InvalidMessage, "invalid failure")
		}
		if value.Details != nil {
			return validateMessage(value.Details)
		}
	case *pb.ResultNotification:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if value.State != "succeeded" && value.State != "failed" && value.State != "cancelled" {
			return NewDecodeError(InvalidMessage, "invalid result state")
		}
		switch value.State {
		case "succeeded":
			if _, ok := value.Outcome.(*pb.ResultNotification_Result); !ok {
				return NewDecodeError(InvalidMessage, "succeeded result requires result object")
			}
		case "failed", "cancelled":
			if _, ok := value.Outcome.(*pb.ResultNotification_Failure); !ok {
				return NewDecodeError(InvalidMessage, "failed result requires failure")
			}
		}
		return validateOutcome(value.Outcome)
	case *pb.HeartbeatRequest:
		return requireID(value.LeaseId, "lease_id")
	case *pb.CancelRequest:
		return requireID(value.OwnerId, "owner_id")
	case *pb.ResumeResultsRequest:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if value.Limit == 0 {
			return NewDecodeError(InvalidMessage, "resume limit must be positive")
		}
	case *pb.ObjectPutRequest:
		if err := requireID(value.TransferId, "transfer_id"); err != nil {
			return err
		}
		if value.Codec == "" || len(value.Sha256) != 32 {
			return NewDecodeError(InvalidMessage, "invalid object put")
		}
	case *pb.ObjectGetRequest:
		if err := requireID(value.TransferId, "transfer_id"); err != nil {
			return err
		}
		if value.Object == nil {
			return NewDecodeError(InvalidMessage, "object is required")
		}
		return validateMessage(value.Object)
	case *pb.ObjectChunk:
		return requireID(value.TransferId, "transfer_id")
	case *pb.StealRequest:
		if value.RequesterNode == "" || value.Limit == 0 {
			return NewDecodeError(InvalidMessage, "invalid steal request")
		}
	case *pb.Ack:
		return validateAck(value)
	case *pb.Error:
		if value.Code == "" || value.Message == "" {
			return NewDecodeError(InvalidMessage, "invalid protocol error")
		}
	}
	return nil
}

func wrapPayload(messageType MessageType, value proto.Message) (*pb.ControlMessage, error) {
	if err := validateMessage(value); err != nil {
		return nil, err
	}
	envelope := &pb.ControlMessage{}
	switch messageType {
	case MessageSubmit:
		switch v := value.(type) {
		case *pb.TaskEnvelope:
			envelope.Body = &pb.ControlMessage_Submit{Submit: v}
		case *pb.ForwardedTask:
			envelope.Body = &pb.ControlMessage_ForwardedSubmit{ForwardedSubmit: v}
		}
	case MessagePull:
		if v, ok := value.(*pb.PullRequest); ok {
			envelope.Body = &pb.ControlMessage_Pull{Pull: v}
		}
	case MessageTask:
		if v, ok := value.(*pb.LeasedTask); ok {
			envelope.Body = &pb.ControlMessage_Task{Task: v}
		}
	case MessageHeartbeat:
		if v, ok := value.(*pb.HeartbeatRequest); ok {
			envelope.Body = &pb.ControlMessage_Heartbeat{Heartbeat: v}
		}
	case MessageResult:
		if v, ok := value.(*pb.ResultNotification); ok {
			envelope.Body = &pb.ControlMessage_Result{Result: v}
		}
	case MessageCancel:
		if v, ok := value.(*pb.CancelRequest); ok {
			envelope.Body = &pb.ControlMessage_Cancel{Cancel: v}
		}
	case MessageComplete:
		switch v := value.(type) {
		case *pb.Completion:
			envelope.Body = &pb.ControlMessage_Complete{Complete: v}
		case *pb.ForwardedCompletion:
			envelope.Body = &pb.ControlMessage_ForwardedComplete{ForwardedComplete: v}
		}
	case MessageSteal:
		if v, ok := value.(*pb.StealRequest); ok {
			envelope.Body = &pb.ControlMessage_Steal{Steal: v}
		}
	case MessageAck:
		if v, ok := value.(*pb.Ack); ok {
			envelope.Body = &pb.ControlMessage_Ack{Ack: v}
		}
	case MessageStatus:
		switch v := value.(type) {
		case *pb.StatusRequest:
			envelope.Body = &pb.ControlMessage_StatusRequest{StatusRequest: v}
		case *pb.StatusSnapshot:
			envelope.Body = &pb.ControlMessage_StatusSnapshot{StatusSnapshot: v}
		}
	case MessageResumeResults:
		if v, ok := value.(*pb.ResumeResultsRequest); ok {
			envelope.Body = &pb.ControlMessage_ResumeResults{ResumeResults: v}
		}
	case MessageError:
		if v, ok := value.(*pb.Error); ok {
			envelope.Body = &pb.ControlMessage_Error{Error: v}
		}
	case MessageObjectPut:
		if v, ok := value.(*pb.ObjectPutRequest); ok {
			envelope.Body = &pb.ControlMessage_ObjectPut{ObjectPut: v}
		}
	case MessageObjectGet:
		if v, ok := value.(*pb.ObjectGetRequest); ok {
			envelope.Body = &pb.ControlMessage_ObjectGet{ObjectGet: v}
		}
	case MessageObjectChunk:
		if v, ok := value.(*pb.ObjectChunk); ok {
			envelope.Body = &pb.ControlMessage_ObjectChunk{ObjectChunk: v}
		}
	case MessageHello:
		if v, ok := value.(*pb.Hello); ok {
			envelope.Body = &pb.ControlMessage_Hello{Hello: v}
		}
	case MessageTaskQuery:
		switch v := value.(type) {
		case *pb.TaskQuery:
			envelope.Body = &pb.ControlMessage_TaskQuery{TaskQuery: v}
		case *pb.TaskSnapshot:
			envelope.Body = &pb.ControlMessage_TaskSnapshot{TaskSnapshot: v}
		}
	case MessageRegisterTasks:
		if v, ok := value.(*pb.TaskRegistration); ok {
			envelope.Body = &pb.ControlMessage_RegisterTasks{RegisterTasks: v}
		}
	}
	if envelope.Body == nil {
		return nil, NewDecodeError(InvalidMessage, fmt.Sprintf("%T is not valid for %s", value, messageType))
	}
	return envelope, nil
}

func EncodePayload(messageType MessageType, value proto.Message) ([]byte, error) {
	envelope, err := wrapPayload(messageType, value)
	if err != nil {
		return nil, err
	}
	return proto.MarshalOptions{Deterministic: true}.Marshal(envelope)
}

func DecodePayload(messageType MessageType, payload []byte) (proto.Message, error) {
	envelope := &pb.ControlMessage{}
	if err := proto.Unmarshal(payload, envelope); err != nil {
		return nil, NewDecodeError(MalformedPayload, err.Error())
	}
	var value proto.Message
	switch body := envelope.Body.(type) {
	case *pb.ControlMessage_Submit:
		if messageType == MessageSubmit {
			value = body.Submit
		}
	case *pb.ControlMessage_ForwardedSubmit:
		if messageType == MessageSubmit {
			value = body.ForwardedSubmit
		}
	case *pb.ControlMessage_Pull:
		if messageType == MessagePull {
			value = body.Pull
		}
	case *pb.ControlMessage_Task:
		if messageType == MessageTask {
			value = body.Task
		}
	case *pb.ControlMessage_Heartbeat:
		if messageType == MessageHeartbeat {
			value = body.Heartbeat
		}
	case *pb.ControlMessage_Result:
		if messageType == MessageResult {
			value = body.Result
		}
	case *pb.ControlMessage_Cancel:
		if messageType == MessageCancel {
			value = body.Cancel
		}
	case *pb.ControlMessage_Complete:
		if messageType == MessageComplete {
			value = body.Complete
		}
	case *pb.ControlMessage_ForwardedComplete:
		if messageType == MessageComplete {
			value = body.ForwardedComplete
		}
	case *pb.ControlMessage_Steal:
		if messageType == MessageSteal {
			value = body.Steal
		}
	case *pb.ControlMessage_Ack:
		if messageType == MessageAck {
			value = body.Ack
		}
	case *pb.ControlMessage_StatusRequest:
		if messageType == MessageStatus {
			value = body.StatusRequest
		}
	case *pb.ControlMessage_StatusSnapshot:
		if messageType == MessageStatus {
			value = body.StatusSnapshot
		}
	case *pb.ControlMessage_ResumeResults:
		if messageType == MessageResumeResults {
			value = body.ResumeResults
		}
	case *pb.ControlMessage_Error:
		if messageType == MessageError {
			value = body.Error
		}
	case *pb.ControlMessage_ObjectPut:
		if messageType == MessageObjectPut {
			value = body.ObjectPut
		}
	case *pb.ControlMessage_ObjectGet:
		if messageType == MessageObjectGet {
			value = body.ObjectGet
		}
	case *pb.ControlMessage_ObjectChunk:
		if messageType == MessageObjectChunk {
			value = body.ObjectChunk
		}
	case *pb.ControlMessage_Hello:
		if messageType == MessageHello {
			value = body.Hello
		}
	case *pb.ControlMessage_TaskQuery:
		if messageType == MessageTaskQuery {
			value = body.TaskQuery
		}
	case *pb.ControlMessage_TaskSnapshot:
		if messageType == MessageTaskQuery {
			value = body.TaskSnapshot
		}
	case *pb.ControlMessage_RegisterTasks:
		if messageType == MessageRegisterTasks {
			value = body.RegisterTasks
		}
	}
	if value == nil {
		return nil, NewDecodeError(InvalidMessage, "protobuf body does not match frame type")
	}
	if err := validateMessage(value); err != nil {
		return nil, err
	}
	return value, nil
}

func NewAck(kind string, fields map[string]interface{}) (*Ack, error) {
	ack := &pb.Ack{Kind: kind}
	for name, raw := range fields {
		switch name {
		case "task_id":
			value, ok := raw.([]byte)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "task_id must be bytes")
			}
			ack.TaskId = value
		case "transfer_id":
			value, ok := raw.([]byte)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "transfer_id must be bytes")
			}
			ack.TransferId = value
		case "lease_id":
			value, ok := raw.([]byte)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "lease_id must be bytes")
			}
			ack.LeaseId = value
		case "owner_id":
			value, ok := raw.([]byte)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "owner_id must be bytes")
			}
			ack.OwnerId = value
		case "cursor":
			value, ok := raw.(uint64)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "cursor must be uint64")
			}
			ack.Cursor = proto.Uint64(value)
		case "cancelled":
			value, ok := raw.(bool)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "cancelled must be bool")
			}
			ack.Cancelled = proto.Bool(value)
		case "object":
			value, ok := raw.(*pb.ObjectRef)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "object must be ObjectRef")
			}
			ack.Object = value
		case "next_cursor":
			value, ok := raw.(uint64)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "next_cursor must be uint64")
			}
			ack.NextCursor = proto.Uint64(value)
		case "more":
			value, ok := raw.(bool)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "more must be bool")
			}
			ack.More = proto.Bool(value)
		case "accepted":
			value, ok := raw.(uint32)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "accepted must be uint32")
			}
			ack.Accepted = proto.Uint32(value)
		case "worker_id":
			value, ok := raw.(string)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "worker_id must be string")
			}
			ack.WorkerId = value
		case "generation":
			value, ok := raw.(uint64)
			if !ok {
				return nil, NewDecodeError(InvalidMessage, "generation must be uint64")
			}
			ack.Generation = proto.Uint64(value)
		default:
			return nil, NewDecodeError(InvalidMessage, "unknown ACK field "+name)
		}
	}
	if err := validateAck(ack); err != nil {
		return nil, err
	}
	return ack, nil
}
