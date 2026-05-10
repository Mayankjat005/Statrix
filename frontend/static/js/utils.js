// This file is a part of Statrix
// Coding : Priyanshu Dey [@irisXDR]

const API_BASE = window.location.origin;


function withCacheBust(endpoint) {
    try {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set('_', Date.now().toString());
        return url.toString();
    } catch (e) {
        return endpoint;
    }
}

function fetchNoCache(endpoint, options = {}) {
    return fetch(withCacheBust(endpoint), { ...options, cache: 'no-store' });
}

async function loadConfig() {
    try {
        const response = await fetch('/api/public/config');
        if (response.ok) {
            const config = await response.json();
            if (config.logo_url) {
                const logoImg = document.querySelector('.logo-header img');
                if (logoImg) {
                    logoImg.src = config.logo_url;
                }
            }
            if (config.status_page_title) {
                document.title = config.status_page_title;
                const pageTitleEl = document.getElementById('page-title');
                if (pageTitleEl) {
                    pageTitleEl.textContent = config.status_page_title;
                }
            }
        }
    } catch (e) {
        console.error('Failed to load config:', e);
    }
}


function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeBool(value) {
    if (value === true || value === false) return value;
    if (value === 1 || value === 0) return value === 1;
    if (typeof value === 'string') {
        const v = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'y', 't'].includes(v)) return true;
        if (['false', '0', 'no', 'n', 'f', ''].includes(v)) return false;
    }
    return Boolean(value);
}


function simplifyOsName(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';

    const lower = raw.toLowerCase();

    if (lower.includes('windows 11')) return 'Windows 11';
    if (lower.includes('windows 10')) return 'Windows 10';
    if (lower.includes('windows server 2025')) return 'Windows Server 2025';
    if (lower.includes('windows server 2022')) return 'Windows Server 2022';
    if (lower.includes('windows server 2019')) return 'Windows Server 2019';
    if (lower.includes('windows server 2016')) return 'Windows Server 2016';
    if (lower.includes('windows server')) return 'Windows Server';
    if (lower.includes('windows')) return 'Windows';
    if (lower.includes('ubuntu')) return 'Ubuntu';
    if (lower.includes('debian')) return 'Debian';
    if (lower.includes('rocky')) return 'Rocky Linux';
    if (lower.includes('almalinux')) return 'AlmaLinux';
    if (lower.includes('centos')) return 'CentOS';
    if (lower.includes('red hat') || lower.includes('rhel')) return 'RHEL';
    if (lower.includes('amazon linux') || lower.includes('amzn')) return 'Amazon Linux';
    if (lower.includes('fedora')) return 'Fedora';
    if (lower.includes('opensuse')) return 'openSUSE';
    if (lower.includes('suse')) return 'SUSE Linux';
    if (lower.includes('linux mint')) return 'Linux Mint';
    if (lower.includes('kali')) return 'Kali Linux';
    if (lower.includes('arch')) return 'Arch Linux';
    if (lower.includes('alpine')) return 'Alpine Linux';
    if (lower.includes('gentoo')) return 'Gentoo';
    if (lower.includes('darwin') || lower.includes('mac os') || lower.includes('macos')) return 'macOS';

    const cleaned = raw
        .replace(/\(.*?\)/g, ' ')
        .replace(/gnu\/linux/ig, '')
        .replace(/\s+/g, ' ')
        .trim();
    if (!cleaned) return raw;
    const primary = cleaned.split(/[,:;/]/)[0].trim();
    return primary || cleaned;
}


function getMonitorTypeLabel(type, heartbeatType = '', compact = false) {
    const hbType = (heartbeatType || '').toLowerCase();
    if (type === 'heartbeat') {
        if (hbType === 'server_agent') {
            return compact ? 'Server Agent' : 'Heartbeat (Server Agent)';
        }
        return compact ? 'Heartbeat' : 'Heartbeat (Cronjob)';
    }
    if (type === 'uptime') {
        return compact ? 'Website' : 'Website Monitor';
    }
    return type;
}


function toTimestampMs(dateStr) {
    if (!dateStr) return null;
    let utcStr = String(dateStr);
    if (!utcStr.endsWith('Z') && !utcStr.includes('+') && !utcStr.includes('-', 10)) {
        utcStr += 'Z';
    }
    const date = new Date(utcStr);
    const ms = date.getTime();
    return Number.isFinite(ms) ? ms : null;
}

function formatDurationFromMs(diffMs) {
    if (!Number.isFinite(diffMs) || diffMs <= 0) return '--';
    const totalMinutes = Math.floor(diffMs / (1000 * 60));
    const days = Math.floor(totalMinutes / (60 * 24));
    const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
    const minutes = totalMinutes % 60;
    if (days > 0) return `${days}d ${hours}hr`;
    if (hours > 0) return `${hours}hr ${minutes}min`;
    return `${minutes}min`;
}

function formatDuration(dateStr) {
    const ts = toTimestampMs(dateStr);
    if (!Number.isFinite(ts) || ts <= 0) return 'Since --';
    const diff = Math.max(0, Date.now() - ts);

    const weeks = Math.floor(diff / (7 * 24 * 60 * 60 * 1000));
    const days = Math.floor((diff % (7 * 24 * 60 * 60 * 1000)) / (24 * 60 * 60 * 1000));
    const hours = Math.floor((diff % (24 * 60 * 60 * 1000)) / (60 * 60 * 1000));
    const minutes = Math.floor((diff % (60 * 60 * 1000)) / (60 * 1000));
    const seconds = Math.floor((diff % (60 * 1000)) / 1000);

    if (weeks > 0) {
        return `Since ${weeks}w ${days}d ago`;
    }
    if (days > 0) {
        return `Since ${days}d ago`;
    }
    if (hours > 0) {
        return `Since ${hours}h ago`;
    }
    if (minutes > 0) {
        return `Since ${minutes}m ago`;
    }
    return `Since ${Math.max(0, seconds)}s ago`;
}

function formatCheckAge(ms) {
    if (!ms) return '--';
    const diffMs = Math.max(0, Date.now() - ms);
    const totalSec = Math.floor(diffMs / 1000);
    if (totalSec < 60) return `${Math.max(1, totalSec)}s`;

    const totalMin = Math.floor(totalSec / 60);
    if (totalMin < 60) return `${totalMin}m`;

    const totalHour = Math.floor(totalMin / 60);
    if (totalHour < 24) return `${totalHour}h`;

    const totalDay = Math.floor(totalHour / 24);
    return `${totalDay}d`;
}

function formatDate(dateStr) {
    const ts = toTimestampMs(dateStr);
    if (!Number.isFinite(ts) || ts <= 0) return '--';
    const date = new Date(ts);
    const day = date.getUTCDate();
    const suffix = getDaySuffix(day);
    const month = date.toLocaleString('en-US', { month: 'long', timeZone: 'UTC' });
    const year = date.getUTCFullYear();
    return `${day}${suffix} of ${month} ${year}`;
}

function getDaySuffix(day) {
    if (day >= 11 && day <= 13) return 'th';
    switch (day % 10) {
        case 1: return 'st';
        case 2: return 'nd';
        case 3: return 'rd';
        default: return 'th';
    }
}

function formatTimeAgo(dateStr) {
    const ts = toTimestampMs(dateStr);
    if (!Number.isFinite(ts) || ts <= 0) return '--';
    const diff = Math.max(0, Math.floor((Date.now() - ts) / 1000));

    if (diff < 60) return diff + 'sec ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
}

function formatIncidentTimestamp(value) {
    if (!value) return '--';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleString();
}
