# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""
slashAI Tools Package

Contains agentic tools for the chatbot, including:
- GitHub documentation reader (read_github_file, list_github_docs)
"""

from tools.github_docs import (
    READ_GITHUB_FILE_TOOL,
    LIST_GITHUB_DOCS_TOOL,
    handle_read_github_file,
    handle_list_github_docs,
    get_reader,
)

__all__ = [
    "READ_GITHUB_FILE_TOOL",
    "LIST_GITHUB_DOCS_TOOL",
    "handle_read_github_file",
    "handle_list_github_docs",
    "get_reader",
]
