package main

import (
	"fmt"
	"net/http"
)

// UserService handles user operations.
type UserService struct {
	db     *Database
	logger *Logger
}

// Reader is an interface for reading data.
type Reader interface {
	Read(p []byte) (n int, err error)
}

// ID is a type alias for string identifiers.
type ID string

// NewUserService creates a new UserService.
func NewUserService(db *Database, logger *Logger) *UserService {
	return &UserService{db: db, logger: logger}
}

// GetUser retrieves a user by ID.
func (s *UserService) GetUser(id int) (*User, error) {
	s.logger.Info("fetching user")
	return s.db.Find(id)
}

// ListUsers returns all users.
func (s *UserService) ListUsers() ([]*User, error) {
	return s.db.FindAll()
}

func main() {
	db := NewDatabase()
	logger := NewLogger()
	svc := NewUserService(db, logger)
	user, err := svc.GetUser(1)
	if err != nil {
		fmt.Println("error:", err)
		return
	}
	fmt.Println("user:", user)
	http.ListenAndServe(":8080", nil)
}
