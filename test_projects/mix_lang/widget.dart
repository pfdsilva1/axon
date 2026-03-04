import 'package:flutter/material.dart';
import 'dart:async';

class MyHomePage extends StatelessWidget {
  final String title;
  final UserService userService;

  MyHomePage({required this.title, required this.userService});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: Center(
        child: UserList(service: userService),
      ),
    );
  }

  void _handleRefresh() {
    userService.refresh();
  }
}

class UserList extends StatefulWidget {
  final UserService service;

  UserList({required this.service});

  @override
  State<UserList> createState() => _UserListState();
}

class _UserListState extends State<UserList> {
  List<User> users = [];

  @override
  void initState() {
    super.initState();
    _loadUsers();
  }

  Future<void> _loadUsers() async {
    final result = await widget.service.getUsers();
    setState(() {
      users = result;
    });
  }

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      itemCount: users.length,
      itemBuilder: (context, index) {
        return ListTile(title: Text(users[index].name));
      },
    );
  }
}

enum UserRole { admin, editor, viewer }

void main() {
  runApp(MaterialApp(home: MyHomePage(title: 'Hello', userService: UserService())));
}
