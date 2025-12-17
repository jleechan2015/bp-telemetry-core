#!/usr/bin/env python3
"""
Minimal CLI handler for Blueplane Telemetry Core.
Handles --cli cursor echo command for testing.
"""

import sys
import argparse


def handle_cursor_echo(args):
    """Handle cursor echo command."""
    if args.message:
        print(args.message)
        return 0
    return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Blueplane Telemetry Core CLI")
    
    # Add --cli flag
    parser.add_argument('--cli', action='store_true', help='CLI mode')
    
    # Parse known args to check for --cli
    known_args, remaining = parser.parse_known_args()
    
    if known_args.cli:
        # Parse remaining args for subcommands
        subparser = argparse.ArgumentParser(description="CLI subcommands")
        subparsers = subparser.add_subparsers(dest='platform', help='Platform commands')
        
        # Cursor subcommands
        cursor_parser = subparsers.add_parser('cursor', help='Cursor platform commands')
        cursor_subparsers = cursor_parser.add_subparsers(dest='command', help='Cursor commands')
        
        # Echo command
        echo_parser = cursor_subparsers.add_parser('echo', help='Echo a message')
        echo_parser.add_argument('message', nargs='?', help='Message to echo')
        
        # Parse the remaining args
        args = subparser.parse_args(remaining)
        
        if args.platform == 'cursor' and args.command == 'echo':
            return handle_cursor_echo(args)
        else:
            subparser.print_help()
            return 1
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
