// Package stubserver implements the Phase 1 stub agent's control socket:
// it decodes frames, runs the pure session validator, and answers
// HELLO/STATUS. It intentionally does not implement task execution,
// storage, or scheduling — those are later phases. AgentHarness uses it
// to prove process lifecycle, readiness, and the framed protocol.
package stubserver

import (
	"net"
	"os"
	"sync"

	"github.com/sanketn26/taskwire/agent/pkg/protocol"
)

const maxPayloadBytes = 16 * 1024 * 1024

// Server accepts framed protocol connections on a Unix domain socket.
type Server struct {
	version  string
	listener net.Listener

	wg sync.WaitGroup

	mu     sync.Mutex
	closed bool
}

// New creates a Server bound to socketPath. The socket file is removed
// first if a stale one is present, since a clean shutdown always removes it.
func New(socketPath, version string) (*Server, error) {
	if err := os.Remove(socketPath); err != nil && !os.IsNotExist(err) {
		return nil, err
	}

	l, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}

	return &Server{version: version, listener: l}, nil
}

// Serve accepts connections until the listener is closed. It returns nil on
// a clean shutdown (Close called) and the underlying error otherwise.
func (s *Server) Serve() error {
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed {
				return nil
			}
			return err
		}

		s.wg.Add(1)
		go s.handle(conn)
	}
}

func (s *Server) handle(conn net.Conn) {
	defer s.wg.Done()
	defer conn.Close()

	state := protocol.NewConnectionState()
	session := protocol.Session{}

	for {
		frame, err := protocol.ReadFrame(conn, maxPayloadBytes)
		if err != nil {
			// Transport-level failure (truncated frame, oversize length,
			// unknown version/type/flags): nothing safe to reply with.
			return
		}
		if frame == nil {
			return // clean EOF
		}

		if authErr := session.Authorize(state, frame.MessageType); authErr != nil {
			de := authErr.(*protocol.DecodeError)
			s.sendError(conn, frame, de)
			if de.Code == protocol.NotRegistered {
				return
			}
			continue
		}

		if beginErr := session.BeginRequest(state, frame.RequestID); beginErr != nil {
			s.sendError(conn, frame, beginErr.(*protocol.DecodeError))
			continue
		}

		if !s.dispatch(conn, state, &session, frame) {
			return
		}
		session.CompleteRequest(state, frame.RequestID)
	}
}

// dispatch handles one authorized frame. It returns false if the
// connection should be closed after this frame.
func (s *Server) dispatch(conn net.Conn, state *protocol.ConnectionState, session *protocol.Session, frame *protocol.Frame) bool {
	value, err := protocol.DecodePayload(frame.MessageType, frame.Payload)
	if err != nil {
		s.sendError(conn, frame, err.(*protocol.DecodeError))
		return true
	}

	switch frame.MessageType {
	case protocol.MessageHello:
		hello := value.(*protocol.Hello)
		session.Register(state, hello.Role, hello.OwnerId, hello.WorkerId)
		ack, _ := protocol.NewAck("hello", nil)
		return s.sendAck(conn, frame, ack)

	case protocol.MessageStatus:
		snapshot := &protocol.StatusSnapshot{
			Version:            s.version,
			Pid:                uint64(os.Getpid()),
			Ready:              true,
			TaskCounts:         map[string]uint64{},
			ActiveLeases:       0,
			WorkerPids:         []uint64{},
			WorkerRestarts:     0,
			StorageHealthy:     true,
			ClusterMembers:     0,
			KafkaOutboxPending: 0,
			LastErrorCode:      nil,
		}
		payload, err := protocol.EncodePayload(protocol.MessageStatus, snapshot)
		if err != nil {
			return false
		}
		return s.sendFrame(conn, protocol.Frame{
			Version: 1, MessageType: protocol.MessageStatus, TaskID: frame.TaskID,
			RequestID: frame.RequestID, Payload: payload,
		})

	default:
		s.sendError(conn, frame, protocol.NewDecodeError(
			protocol.Internal, "not implemented by the phase 1 stub server",
		))
		return true
	}
}

func (s *Server) sendAck(conn net.Conn, frame *protocol.Frame, ack *protocol.Ack) bool {
	payload, err := protocol.EncodePayload(protocol.MessageAck, ack)
	if err != nil {
		return false
	}
	return s.sendFrame(conn, protocol.Frame{
		Version: 1, MessageType: protocol.MessageAck, TaskID: frame.TaskID,
		RequestID: frame.RequestID, Payload: payload,
	})
}

func (s *Server) sendError(conn net.Conn, frame *protocol.Frame, de *protocol.DecodeError) bool {
	errPayload := &protocol.Error{
		Code: de.Code, Message: de.Message,
		Retryable: protocol.ErrorRetryable[de.Code], Details: map[string]string{},
	}
	payload, err := protocol.EncodePayload(protocol.MessageError, errPayload)
	if err != nil {
		return false
	}
	return s.sendFrame(conn, protocol.Frame{
		Version: 1, MessageType: protocol.MessageError, TaskID: frame.TaskID,
		RequestID: frame.RequestID, Flags: protocol.FlagError, Payload: payload,
	})
}

func (s *Server) sendFrame(conn net.Conn, frame protocol.Frame) bool {
	return protocol.WriteFrame(conn, frame, maxPayloadBytes) == nil
}

// Close stops accepting new connections, waits for in-flight handlers, and
// removes the socket file so no owned socket is left behind.
func (s *Server) Close() error {
	s.mu.Lock()
	s.closed = true
	s.mu.Unlock()

	err := s.listener.Close()
	s.wg.Wait()
	_ = os.Remove(s.listener.Addr().String())
	return err
}
