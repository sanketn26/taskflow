package protocol

// Control-plane schemas are generated from proto/taskwire/v1/control.proto.
// gRPC selects the message type per RPC, so this file carries only semantic
// validation — the constraints protobuf itself cannot express (16-byte IDs,
// 32-byte checksums, enumerated string fields, required oneof branches).
// MsgPack is not used here.

import (
	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/proto"
)

type ObjectRef = pb.ObjectRef
type ValueRef = pb.ValueRef
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
type Error = pb.Error
type HeartbeatRequest = pb.HeartbeatRequest
type CancelRequest = pb.CancelRequest
type ObjectGetRequest = pb.ObjectGetRequest
type ObjectChunk = pb.ObjectChunk
type StealRequest = pb.StealRequest
type StatusRequest = pb.StatusRequest
type WorkerMessage = pb.WorkerMessage
type AgentMessage = pb.AgentMessage
type WorkerRegistration = pb.WorkerRegistration
type WatchResultsRequest = pb.WatchResultsRequest
type AckResultRequest = pb.AckResultRequest

func requireID(value []byte, field string) error {
	if len(value) != 16 {
		return NewProtocolError(InvalidMessage, field+" must be 16 bytes")
	}
	return nil
}

func requireNonEmpty(value, field string) error {
	if value == "" {
		return NewProtocolError(InvalidMessage, field+" must not be empty")
	}
	return nil
}

func uniqueStrings(values []string, field string) error {
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if value == "" || seen[value] {
			return NewProtocolError(InvalidMessage, field+" must contain unique non-empty strings")
		}
		seen[value] = true
	}
	return nil
}

func validateOutcome(outcome interface{}) error {
	switch value := outcome.(type) {
	case *pb.Completion_Result:
		return Validate(value.Result)
	case *pb.Completion_Failure:
		return Validate(value.Failure)
	case *pb.ForwardedCompletion_Result:
		return Validate(value.Result)
	case *pb.ForwardedCompletion_Failure:
		return Validate(value.Failure)
	case *pb.ResultNotification_Result:
		return Validate(value.Result)
	case *pb.ResultNotification_Failure:
		return Validate(value.Failure)
	default:
		return NewProtocolError(InvalidMessage, "result/failure outcome is required")
	}
}

// Validate enforces Taskwire's semantic constraints on a decoded message.
// gRPC has already guaranteed the message parses and matches the RPC's
// declared type, so this is purely about meaning.
func Validate(message proto.Message) error {
	switch value := message.(type) {
	case *pb.WorkerRegistration:
		if value.WorkerId == "" || value.RuntimeVersion == "" || value.SdkVersion == "" || len(value.Codecs) == 0 {
			return NewProtocolError(InvalidMessage, "worker registration identity is incomplete")
		}
		if value.Runtime != "python" && value.Runtime != "nodejs" && value.Runtime != "go" {
			return NewProtocolError(InvalidMessage, "invalid worker runtime")
		}
		if err := uniqueStrings(value.Codecs, "codecs"); err != nil {
			return err
		}
		if value.Tasks == nil {
			return NewProtocolError(InvalidMessage, "worker registration requires a task set")
		}
		return Validate(value.Tasks)
	case *pb.ObjectRef:
		if value.Store == "" || value.Key == "" || value.Codec == "" || len(value.Sha256) != 32 {
			return NewProtocolError(InvalidMessage, "invalid ObjectRef")
		}
	case *pb.ValueRef:
		switch location := value.Location.(type) {
		case *pb.ValueRef_Inline:
			if value.Codec == "" {
				return NewProtocolError(InvalidMessage, "inline ValueRef requires codec")
			}
		case *pb.ValueRef_Object:
			if value.Codec != "" {
				return NewProtocolError(InvalidMessage, "object ValueRef cannot declare codec")
			}
			return Validate(location.Object)
		default:
			return NewProtocolError(InvalidMessage, "ValueRef location is required")
		}
	case *pb.TaskRegistration:
		if value.WorkerId == "" || value.Generation == 0 {
			return NewProtocolError(InvalidMessage, "invalid task registration")
		}
		seen := make(map[string]bool)
		for _, task := range value.Tasks {
			if task == nil {
				return NewProtocolError(InvalidMessage, "nil task capability")
			}
			key := task.TaskName + "\x00" + task.TaskVersion
			if task.TaskName == "" || task.TaskVersion == "" || (task.Invocation != "value" && task.Invocation != "python_args") || len(task.Codecs) == 0 || seen[key] {
				return NewProtocolError(InvalidMessage, "invalid task capability")
			}
			if err := uniqueStrings(task.Codecs, "task codecs"); err != nil {
				return err
			}
			seen[key] = true
		}
	case *pb.PullRequest:
		if value.WorkerId == "" || value.CapabilityGeneration == 0 {
			return NewProtocolError(InvalidMessage, "invalid pull request")
		}
	case *pb.TaskQuery:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if len(value.TaskIds) == 0 {
			return NewProtocolError(InvalidMessage, "task_ids must not be empty")
		}
		for _, id := range value.TaskIds {
			if err := requireID(id, "task_id"); err != nil {
				return err
			}
		}
	case *pb.TaskSnapshot:
		for _, task := range value.Tasks {
			if task == nil {
				return NewProtocolError(InvalidMessage, "nil task snapshot entry")
			}
			if err := Validate(task); err != nil {
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
				return NewProtocolError(InvalidMessage, "nonterminal task snapshot has terminal fields")
			}
		case "succeeded":
			result, ok := value.Outcome.(*pb.TaskSnapshotEntry_Result)
			if value.Cursor == nil || !ok {
				return NewProtocolError(InvalidMessage, "succeeded snapshot requires result and cursor")
			}
			return Validate(result.Result)
		case "failed", "cancelled", "dead_lettered":
			failure, ok := value.Outcome.(*pb.TaskSnapshotEntry_Failure)
			if value.Cursor == nil || !ok {
				return NewProtocolError(InvalidMessage, "failed snapshot requires failure and cursor")
			}
			return Validate(failure.Failure)
		default:
			return NewProtocolError(InvalidMessage, "invalid task snapshot state")
		}
	case *pb.TaskEnvelope:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if value.TaskName == "" || value.TaskVersion == "" || (value.Invocation != "value" && value.Invocation != "python_args") || value.Input == nil {
			return NewProtocolError(InvalidMessage, "invalid task envelope")
		}
		return Validate(value.Input)
	case *pb.LeasedTask:
		if value.Task == nil || value.TtlMs == 0 || value.Attempt == 0 {
			return NewProtocolError(InvalidMessage, "invalid leased task")
		}
		if err := requireID(value.TaskId, "task_id"); err != nil {
			return err
		}
		if err := requireID(value.LeaseId, "lease_id"); err != nil {
			return err
		}
		return Validate(value.Task)
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
			return NewProtocolError(InvalidMessage, "invalid forwarded task")
		}
		return Validate(value.Task)
	case *pb.ForwardedCompletion:
		if err := requireID(value.TransferId, "transfer_id"); err != nil {
			return err
		}
		if value.RemoteNode == "" || value.RemoteAttempt == 0 {
			return NewProtocolError(InvalidMessage, "invalid forwarded completion")
		}
		return validateOutcome(value.Outcome)
	case *pb.Failure:
		if value.Code == "" || value.Message == "" {
			return NewProtocolError(InvalidMessage, "invalid failure")
		}
		if value.Details != nil {
			return Validate(value.Details)
		}
	case *pb.ResultNotification:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		if err := requireID(value.TaskId, "task_id"); err != nil {
			return err
		}
		if value.State != "succeeded" && value.State != "failed" && value.State != "cancelled" {
			return NewProtocolError(InvalidMessage, "invalid result state")
		}
		switch value.State {
		case "succeeded":
			if _, ok := value.Outcome.(*pb.ResultNotification_Result); !ok {
				return NewProtocolError(InvalidMessage, "succeeded result requires result object")
			}
		case "failed", "cancelled":
			if _, ok := value.Outcome.(*pb.ResultNotification_Failure); !ok {
				return NewProtocolError(InvalidMessage, "failed result requires failure")
			}
		}
		return validateOutcome(value.Outcome)
	case *pb.HeartbeatRequest:
		return requireID(value.LeaseId, "lease_id")
	case *pb.CancelRequest:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		return requireID(value.TaskId, "task_id")
	case *pb.WatchResultsRequest:
		return requireID(value.OwnerId, "owner_id")
	case *pb.AckResultRequest:
		if err := requireID(value.OwnerId, "owner_id"); err != nil {
			return err
		}
		return requireID(value.TaskId, "task_id")
	case *pb.ObjectGetRequest:
		if value.Object == nil {
			return NewProtocolError(InvalidMessage, "object is required")
		}
		return Validate(value.Object)
	case *pb.ObjectChunk:
		if value.Sha256 != nil && len(value.Sha256) != 32 {
			return NewProtocolError(InvalidMessage, "sha256 must be 32 bytes")
		}
	case *pb.StealRequest:
		if value.RequesterNode == "" || value.Limit == 0 {
			return NewProtocolError(InvalidMessage, "invalid steal request")
		}
	case *pb.Error:
		if value.Code == "" || value.Message == "" {
			return NewProtocolError(InvalidMessage, "invalid protocol error")
		}
	}
	return nil
}
