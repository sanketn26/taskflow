// Package stubserver implements the Phase 0 stub agent's control socket.
// It exists only to prove that AgentHarness can start a process, observe a
// ready socket, exchange a status call, and shut the process down cleanly.
// Later phases replace this with the real IPC protocol.
package stubserver

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"strings"
	"sync"
)

// Server accepts newline-delimited text commands on a Unix domain socket.
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
		return nil, fmt.Errorf("remove stale socket %s: %w", socketPath, err)
	}

	l, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, fmt.Errorf("listen on %s: %w", socketPath, err)
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

	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		cmd := strings.TrimSpace(scanner.Text())
		switch cmd {
		case "STATUS":
			fmt.Fprintf(conn, "OK version=%s pid=%d\n", s.version, os.Getpid())
		case "":
			// ignore blank lines
		default:
			fmt.Fprintf(conn, "ERR unknown command %q\n", cmd)
		}
	}
	_ = scanner.Err() // connection closed or read error; nothing to report to a peer that's gone
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
